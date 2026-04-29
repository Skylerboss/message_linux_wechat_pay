#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Linux微信支付回调系统 - 主程序
整合DBus监听、OCR扫描和回调发送
"""

import os
import sys
import json
import time
import signal
import logging
import argparse
import threading
import re
import hashlib
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_FILE = Path(os.environ.get("APP_LOG_FILE") or BASE_DIR / "wechat_pay.log")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def _get_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name, "")
    if not raw_value:
        return default

    try:
        parsed_value = int(raw_value)
    except ValueError:
        return default

    if parsed_value <= 0:
        return default
    return parsed_value


def configure_logging() -> None:
    handlers: List[logging.Handler] = []
    should_disable_stream = os.environ.get("WECHAT_PAY_DISABLE_STREAM_LOG") == "1"
    log_max_bytes = _get_int_env("APP_LOG_MAX_BYTES", 20 * 1024 * 1024)
    log_backup_count = _get_int_env("APP_LOG_BACKUP_COUNT", 5)

    if not should_disable_stream:
        handlers.append(logging.StreamHandler())

    handlers.append(
        RotatingFileHandler(
            DEFAULT_LOG_FILE,
            maxBytes=log_max_bytes,
            backupCount=log_backup_count,
            encoding="utf-8",
        )
    )

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)


configure_logging()
logger = logging.getLogger(__name__)

# 导入模块
try:
    from config import ConfigManager, init_config, get_config
    from dbus_listener import create_listener, PaymentNotification, HAS_DBUS
    from db_monitor import WeChatSessionDatabaseMonitor, DatabasePaymentResult
    from message_monitor import WeChatMessageDatabaseMonitor, DatabaseMessageResult
    from ocr_scanner import WeChatOCRScanner, OCRPaymentResult, HAS_CV2
    from callback_server import CallbackSender, LocalCallbackServer, PaymentCallback
    from order_manager import OrderManager

    # 检查是否支持Linux
    if sys.platform == "linux":
        logger.info("Running on Linux platform")
    else:
        logger.warning(
            f"Running on {sys.platform}, some features may not work properly"
        )

except ImportError as e:
    logger.error(f"Failed to import required modules: {e}")
    logger.error("Please install dependencies: pip install -r requirements.txt")
    sys.exit(1)


class WeChatPayCallbackSystem:
    """
    Linux微信支付回调系统主类
    """

    def __init__(self):
        self.config = get_config()
        self.running = False

        # 组件
        self.dbus_listener = None
        self.db_monitor = None
        self.message_monitor = None
        self.ocr_scanner = None
        self.callback_sender = None
        self.local_server = None
        self.order_manager = None

        # 统计
        self.stats = {
            "payments_detected": 0,
            "callbacks_sent": 0,
            "callbacks_failed": 0,
            "start_time": None,
        }

        # 重复支付检测缓存 {key: timestamp}
        self._recent_payments = {}
        self._dedup_window = 30  # 30秒内相同金额不重复处理
        self._ocr_provisional_window = 10  # OCR未提取到到账时间时的临时去重窗口

        # 初始化
        self._init_components()

    def _init_components(self):
        """初始化各组件"""
        # 订单管理器
        self.order_manager = OrderManager(self.config.order_config)

        # DBus监听器
        if self.config.dbus.enabled:
            try:
                self.dbus_listener = create_listener(
                    callback=self._on_payment_from_dbus, use_mock=not HAS_DBUS
                )
                logger.info("DBus listener initialized")
            except Exception as e:
                logger.error(f"Failed to initialize DBus listener: {e}")

        # OCR 已停用，当前支付主链路改为数据库检测

        # 解密数据库监听器（推荐主检测方式）
        if self.config.database_monitor.enabled:
            try:
                self.db_monitor = WeChatSessionDatabaseMonitor(
                    session_db_path=self.config.database_monitor.session_db_path,
                    callback=self._on_payment_from_database,
                    decrypt_project_dir=self.config.database_monitor.decrypt_project_dir,
                    poll_interval=self.config.database_monitor.poll_interval,
                    summary_keywords=self.config.database_monitor.summary_keywords,
                    trusted_usernames=self.config.database_monitor.trusted_usernames,
                    trusted_sender_names=self.config.database_monitor.trusted_sender_names,
                )
                logger.info("Session DB monitor initialized")
            except Exception as e:
                logger.error(f"Failed to initialize session DB monitor: {e}")

        # 普通消息数据库监听器
        if (
            self.config.message_monitor.enabled
            and self.config.message_monitor.callback_url
        ):
            try:
                self.message_monitor = WeChatMessageDatabaseMonitor(
                    decrypt_project_dir=self.config.message_monitor.decrypt_project_dir,
                    callback_url=self.config.message_monitor.callback_url,
                    callback=self._on_message_from_database,
                    secret_key=self.config.message_monitor.secret_key,
                    poll_interval=self.config.message_monitor.poll_interval,
                    include_message_types=self.config.message_monitor.include_message_types,
                )
                logger.info("Message DB monitor initialized")
            except Exception as e:
                logger.error(f"Failed to initialize message DB monitor: {e}")

        # 回调发送器
        if self.config.callback.enabled and self.config.callback.url:
            self.callback_sender = CallbackSender(
                callback_url=self.config.callback.url,
                secret_key=self.config.callback.secret_key,
                timeout=self.config.callback.timeout,
                retry_count=self.config.callback.retry_count,
                retry_delay=self.config.callback.retry_delay,
            )
            logger.info(
                f"Callback sender initialized (URL: {self.config.callback.url})"
            )

        # 本地服务器
        if self.config.server.enabled:
            self.local_server = LocalCallbackServer(
                host=self.config.server.host, port=self.config.server.port
            )
            self.local_server.set_payment_callback(self._on_payment_received)
            logger.info(
                f"Local server initialized on {self.config.server.host}:{self.config.server.port}"
            )

    def _on_payment_from_dbus(self, notification: PaymentNotification):
        """处理DBus监听到的支付通知"""
        logger.info(
            f"Payment detected via DBus: {notification.amount}元 from {notification.payer}"
        )

        # 关联订单
        order_id = self._match_order(notification.amount, notification.payer)
        notification.order_id = order_id

        # 发送回调
        self._send_callback(notification)

        self.stats["payments_detected"] += 1

    def _on_payment_from_ocr(self, result: OCRPaymentResult):
        """处理OCR识别到的支付"""
        logger.info(
            f"Payment detected via OCR: {result.amount}元 from {result.payer}, time={result.extracted_time}"
        )

        payment_keys, dedup_reason = self._build_ocr_payment_keys(result)

        if any(payment_key in self._recent_payments for payment_key in payment_keys):
            logger.info(
                f"Duplicate payment ignored: {result.amount}元, reason={dedup_reason}"
            )
            return

        # 记录本次支付
        current_time = time.time()
        for payment_key in payment_keys:
            self._recent_payments[payment_key] = current_time

        # 清理过期记录（保留最近30分钟的）
        self._cleanup_recent_payments()

        # 关联订单
        order_id = self._match_order(result.amount, result.payer)

        # 创建PaymentNotification对象
        notification = PaymentNotification(
            amount=result.amount,
            payer=result.payer,
            timestamp=result.timestamp,
            raw_text=result.raw_text,
            source="ocr_screenshot",
        )
        notification.order_id = order_id

        # 发送回调
        self._send_callback(notification)

        self.stats["payments_detected"] += 1

    def _on_payment_from_database(self, result: DatabasePaymentResult):
        """处理数据库监听到的支付摘要"""
        logger.info(
            "Payment detected via DB: %s元 from %s, username=%s, summary=%s",
            result.amount,
            result.payer,
            result.username,
            result.summary,
        )

        order_id = self._match_order(result.amount, result.payer)

        notification = PaymentNotification(
            amount=result.amount,
            payer=result.payer,
            timestamp=result.timestamp,
            raw_text=json.dumps(result.to_dict(), ensure_ascii=False),
            source=result.source,
        )
        notification.order_id = order_id

        # 跨源去重：金额+时间戳作为全局去重key（session_db 和 biz_message_db 共享）
        cross_source_key = f"cross::{result.amount:.2f}::{result.last_timestamp}"
        if cross_source_key in self._recent_payments:
            logger.info(
                "Cross-source duplicate payment ignored: %s元, source=%s, timestamp=%s",
                result.amount,
                result.source,
                result.last_timestamp,
            )
            return

        # 本源去重
        dedup_key = (
            f"{result.source}::{result.username}::{result.last_timestamp}::{result.amount:.2f}"
        )
        if dedup_key in self._recent_payments:
            logger.info("Duplicate database payment ignored: %s", dedup_key)
            return

        self._recent_payments[cross_source_key] = time.time()
        self._recent_payments[dedup_key] = time.time()
        self._cleanup_recent_payments()

        self._send_callback(notification)
        self.stats["payments_detected"] += 1

    def _on_message_from_database(self, result: DatabaseMessageResult):
        """处理数据库监听到的普通消息。"""
        logger.info(
            "Message detected via DB: user=%s type=%s content=%s",
            result.user_name,
            result.local_type,
            result.content,
        )

    def _cleanup_recent_payments(self):
        """清理OCR去重缓存"""
        current_time = time.time()
        self._recent_payments = {
            k: v for k, v in self._recent_payments.items() if current_time - v < 1800
        }

    def _match_order(self, amount: float, payer: str) -> Optional[str]:
        """匹配订单，订单管理器不可用时安全降级"""
        if not self.order_manager:
            logger.warning(
                f"Order manager unavailable, skipping order match: amount={amount}, payer={payer}"
            )
            return None

        return self.order_manager.match_order(amount=amount, payer=payer)

    def _normalize_payment_time(self, extracted_time: Optional[str]) -> Optional[str]:
        """标准化到账时间字符串"""
        if not extracted_time:
            return None

        cleaned_time = re.sub(r"\s+", " ", extracted_time.strip())
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed_time = datetime.strptime(cleaned_time, fmt)
                return parsed_time.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

        return cleaned_time

    def _extract_payment_time_from_text(self, raw_text: str) -> Optional[str]:
        """从OCR原始文本中兜底提取到账时间"""
        if not raw_text:
            return None

        time_match = re.search(
            r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)", raw_text
        )
        if not time_match:
            return None

        return self._normalize_payment_time(time_match.group(1))

    def _build_ocr_time_key(self, amount: float, payer: str, payment_time: str) -> str:
        """构建基于到账时间的OCR去重键"""
        return f"ocr_time::{amount:.2f}::{payer}::{payment_time}"

    def _build_ocr_text_fingerprint(self, raw_text: str) -> str:
        """构建OCR文本指纹，减少同一屏重复回调"""
        normalized_text = re.sub(r"\s+", "", raw_text or "")
        return hashlib.sha1(normalized_text.encode("utf-8")).hexdigest()[:16]

    def _build_ocr_payment_keys(
        self, result: OCRPaymentResult
    ) -> Tuple[List[str], str]:
        """优先按到账时间去重，并增加文本指纹兜底"""
        normalized_time = self._normalize_payment_time(
            result.extracted_time
        ) or self._extract_payment_time_from_text(result.raw_text)
        text_fingerprint = self._build_ocr_text_fingerprint(result.raw_text)
        rolling_text_key = f"ocr_text_rolling::{result.amount:.2f}::{text_fingerprint}"

        if normalized_time:
            return [
                self._build_ocr_time_key(result.amount, result.payer, normalized_time),
                f"ocr_text::{result.amount:.2f}::{result.payer}::{normalized_time}::{text_fingerprint}",
                rolling_text_key,
            ], (
                f"time={normalized_time}, fingerprint={text_fingerprint}, "
                f"rolling_key={rolling_text_key}"
            )

        provisional_bucket = int(time.time() / self._ocr_provisional_window)
        provisional_key = f"ocr_provisional::{result.amount:.2f}::{result.payer}::{provisional_bucket}"
        text_key = f"ocr_text::{result.amount:.2f}::{result.payer}::{text_fingerprint}::{provisional_bucket}"
        return [provisional_key, text_key, rolling_text_key], (
            f"provisional_window={self._ocr_provisional_window}s, fingerprint={text_fingerprint}, "
            f"rolling_key={rolling_text_key}"
        )

    def _on_payment_received(self, payment_data: Dict[str, Any]):
        """本地服务器收到支付通知时的回调"""
        logger.debug(f"Payment received via local server: {payment_data}")

    def _send_callback(self, notification: PaymentNotification):
        """发送支付回调"""
        if not self.callback_sender:
            logger.warning("No callback sender configured")
            return

        # 构建回调数据
        payment_data = PaymentCallback(
            order_id=notification.order_id,
            amount=notification.amount,
            payer=notification.payer,
            timestamp=notification.timestamp.isoformat(),
            payment_method="wechat",
            source=notification.source,
            raw_data=notification.to_dict(),
        )

        # 发送
        success = self.callback_sender.send(payment_data)

        if success:
            self.stats["callbacks_sent"] += 1
            logger.info(f"Callback sent successfully for order {notification.order_id}")
        else:
            self.stats["callbacks_failed"] += 1
            logger.error(f"Callback failed for order {notification.order_id}")

    def _get_ocr_scan_region(self) -> Optional[Tuple[int, int, int, int]]:
        """解析OCR扫描区域配置"""
        scan_region = self.config.ocr.scan_region
        if scan_region is None:
            return None

        if not isinstance(scan_region, list) or len(scan_region) != 4:
            logger.warning(
                "Invalid ocr.scan_region configured, expected [x, y, width, height]"
            )
            return None

        try:
            x, y, width, height = [int(value) for value in scan_region]
        except (TypeError, ValueError):
            logger.warning("Invalid ocr.scan_region values, all items must be integers")
            return None

        if width <= 0 or height <= 0:
            logger.warning(
                "Invalid ocr.scan_region size, width and height must be positive"
            )
            return None

        return (x, y, width, height)

    def start(self):
        """启动系统"""
        logger.info("=" * 50)
        logger.info("Linux WeChat Pay Callback System Starting...")
        logger.info("=" * 50)

        self.running = True
        self.stats["start_time"] = datetime.now()

        # 启动本地服务器
        if self.local_server:
            self.local_server.start(blocking=False)
            time.sleep(0.5)  # 等待服务器启动

        # 启动DBus监听器（后台线程）
        if self.dbus_listener:
            logger.info("Starting DBus listener...")
            if HAS_DBUS:
                dbus_thread = self.dbus_listener.run_async()
            else:
                # 模拟模式下需要特殊处理
                dbus_thread = threading.Thread(target=self.dbus_listener.start)
                dbus_thread.daemon = True
                dbus_thread.start()

        # 启动数据库监听器（推荐主检测方式）
        if self.db_monitor:
            logger.info("Starting session DB monitor...")
            self.db_monitor.start()

        if self.message_monitor:
            logger.info("Starting message DB monitor...")
            self.message_monitor.start()

        # OCR 已停用，避免无意义的持续扫描占用资源

        logger.info("System started successfully!")
        logger.info(f"Monitoring for WeChat payments...")

        if not self.config.callback.url:
            logger.warning("No callback URL configured, payments will not be forwarded")
            logger.info("Please set callback.url in config.yaml")

        # 主循环
        self._main_loop()

    def _main_loop(self):
        """主循环"""
        try:
            while self.running:
                time.sleep(1)

                # 可以在这里添加定期检查任务
                # 如：检查微信进程是否运行等

        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        finally:
            self.stop()

    def stop(self):
        """停止系统"""
        if not self.running:
            return

        logger.info("Stopping system...")
        self.running = False

        if self.dbus_listener:
            self.dbus_listener.stop()

        if self.db_monitor:
            self.db_monitor.stop()

        if self.message_monitor:
            self.message_monitor.stop()

        if self.local_server:
            self.local_server.stop()

        # 打印统计
        self._print_stats()

        logger.info("System stopped")

    def _print_stats(self):
        """打印运行统计"""
        if self.stats["start_time"]:
            duration = datetime.now() - self.stats["start_time"]
            logger.info("=" * 50)
            logger.info("Run Statistics:")
            logger.info(f"  Duration: {duration}")
            logger.info(f"  Payments detected: {self.stats['payments_detected']}")
            logger.info(f"  Callbacks sent: {self.stats['callbacks_sent']}")
            logger.info(f"  Callbacks failed: {self.stats['callbacks_failed']}")
            if self.callback_sender:
                cb_stats = self.callback_sender.get_stats()
                logger.info(
                    f"  Callback success rate: {cb_stats['success']}/{cb_stats['total']}"
                )
            logger.info("=" * 50)


def create_default_config():
    """创建默认配置文件"""
    manager = ConfigManager()
    config_content = manager.create_default_config()

    with open("config.yaml", "w", encoding="utf-8") as f:
        f.write(config_content)

    print("Created config.yaml")
    print("Please edit config.yaml and set your callback URL")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Linux WeChat Pay Callback System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Run with default config
  %(prog)s -c /path/to/config.yaml  # Run with custom config
  %(prog)s --init                   # Create default config file
  %(prog)s --test-callback          # Test callback without listening
        """,
    )

    parser.add_argument(
        "-c", "--config", help="Path to configuration file", default=None
    )

    parser.add_argument(
        "--init", action="store_true", help="Create default configuration file and exit"
    )

    parser.add_argument(
        "--test-callback",
        action="store_true",
        help="Test callback without starting listeners",
    )

    parser.add_argument("--callback-url", help="Override callback URL from config")

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    # 初始化配置
    if args.init:
        create_default_config()
        return 0

    # 加载配置
    init_config(args.config)
    config = get_config()

    # 设置日志级别
    if args.verbose or config.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(getattr(logging, config.log_level.upper()))

    # 覆盖回调URL
    if args.callback_url:
        config.callback.url = args.callback_url

    # 测试回调
    if args.test_callback:
        test_payment = PaymentCallback(
            order_id="TEST001",
            amount=30.01,
            payer="测试用户",
            timestamp=datetime.now().isoformat(),
            payment_method="wechat",
            source="manual_test",
            raw_data={"test": True},
        )

        if config.callback.url:
            sender = CallbackSender(
                callback_url=config.callback.url, secret_key=config.callback.secret_key
            )
            result = sender.send(test_payment)
            print(f"Test callback result: {'SUCCESS' if result else 'FAILED'}")
        else:
            print("No callback URL configured")
        return 0

    # 检查微信是否运行
    try:
        import psutil

        wechat_running = False
        for proc in psutil.process_iter(["name"]):
            try:
                if "wechat" in proc.info["name"].lower():
                    wechat_running = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if not wechat_running:
            logger.warning(
                "WeChat process not detected. Please make sure WeChat is running."
            )
    except Exception as e:
        logger.debug(f"Could not check WeChat process: {e}")

    # 启动系统
    system = WeChatPayCallbackSystem()

    # 设置信号处理
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        system.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        system.start()
    except Exception as e:
        logger.error(f"System error: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
