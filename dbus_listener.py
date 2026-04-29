#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DBus通知监听器 - 监听微信支付通知
"""

import re
import json
import logging
import subprocess
import threading
from datetime import datetime
from typing import Callable, Optional, Dict, Any, List

try:
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    HAS_DBUS = True
except ImportError:
    HAS_DBUS = False
    logging.warning("DBus not available, notification monitoring disabled")

logger = logging.getLogger(__name__)


class PaymentNotification:
    """支付通知数据模型"""

    def __init__(
        self,
        amount: float,
        payer: str,
        timestamp: datetime,
        raw_text: str,
        source: str = "notification",
    ):
        self.amount = amount
        self.payer = payer
        self.timestamp = timestamp
        self.raw_text = raw_text
        self.source = source
        self.order_id: Optional[str] = None  # 可后续关联订单

    def to_dict(self) -> Dict[str, Any]:
        return {
            "amount": self.amount,
            "payer": self.payer,
            "timestamp": self.timestamp.isoformat(),
            "raw_text": self.raw_text,
            "source": self.source,
            "order_id": self.order_id,
        }

    def __repr__(self):
        return f"<PaymentNotification amount={self.amount} payer={self.payer}>"


class WeChatDBusListener:
    """
    微信DBus通知监听器
    监听系统桌面通知，识别微信支付收款消息
    """

    # 微信支付通知的特征模式
    PAYMENT_PATTERNS = [
        # 标准收款通知
        r"微信收款.*?(\d+\.?\d*)元",
        r"收到.*?(\d+\.?\d*)元",
        r"收款.*?([\d.]+).*?元",
        # 赞赏码通知
        r"赞赏.*?([\d.]+).*?元",
        # 二维码收款
        r"二维码收款.*?(\d+\.?\d*)",
    ]

    # 付款人提取模式
    PAYER_PATTERNS = [
        r"来自(.*?)[,\s]",
        r"(.*?)向你付款",
        r"付款人[：:](.*?)[,\s]",
        r"(.*?)的收款",
    ]

    def __init__(
        self, callback: Optional[Callable[[PaymentNotification], None]] = None
    ):
        self.callback = callback
        self.running = False
        self.loop = None
        self.session_bus = None
        self._notification_matches = []
        self._monitor_process: Optional[subprocess.Popen] = None
        self._monitor_thread: Optional[threading.Thread] = None

        if not HAS_DBUS:
            raise RuntimeError("DBus not available on this system")

    def _parse_payment(self, title: str, body: str) -> Optional[PaymentNotification]:
        """
        解析通知内容，提取支付信息
        """
        full_text = f"{title} {body}"

        # 检查是否包含支付关键词
        if not any(kw in full_text for kw in ["收款", "支付", "赞赏", "收到", "入账"]):
            return None

        # 提取金额
        amount = None
        for pattern in self.PAYMENT_PATTERNS:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                try:
                    amount = float(match.group(1))
                    break
                except ValueError:
                    continue

        if amount is None:
            # 尝试通用金额匹配
            amount_match = re.search(r"(\d+\.\d{1,2})", full_text)
            if amount_match:
                try:
                    amount = float(amount_match.group(1))
                except ValueError:
                    pass

        if amount is None:
            return None

        # 提取付款人
        payer = "未知"
        for pattern in self.PAYER_PATTERNS:
            match = re.search(pattern, full_text)
            if match:
                payer = match.group(1).strip()
                break

        # 如果付款人是未知，尝试从body中提取第一个非空字段
        if payer == "未知" and body:
            lines = [l.strip() for l in body.split("\n") if l.strip()]
            if len(lines) >= 2:
                payer = lines[0]

        notification = PaymentNotification(
            amount=amount,
            payer=payer,
            timestamp=datetime.now(),
            raw_text=full_text,
            source="dbus_notification",
        )

        logger.info(f"[PAYMENT DETECTED] {notification}")
        return notification

    def _is_wechat_notification(self, app_name: str, summary: str, body: str) -> bool:
        """判断是否为微信相关通知"""
        full_text = f"{app_name} {summary} {body}".lower()
        return any(keyword in full_text for keyword in ["微信", "wechat", "weixin pay"])

    def _handle_notification_payload(self, app_name: str, summary: str, body: str):
        """处理解析后的通知载荷"""
        try:
            if not self._is_wechat_notification(app_name, summary, body):
                return

            logger.debug(
                "WeChat notification captured: app=%s summary=%s body=%s",
                app_name,
                summary,
                body,
            )

            payment = self._parse_payment(summary, body)
            if payment and self.callback:
                self.callback(payment)

        except Exception as e:
            logger.error(f"Error processing notification: {e}")

    def _extract_string_value(self, line: str) -> Optional[str]:
        """从 dbus-monitor 输出中提取 string 值"""
        match = re.search(r'string\s+"(.*)"', line)
        if not match:
            return None

        value = match.group(1)
        return bytes(value, "utf-8").decode("unicode_escape")

    def _parse_notify_block(self, block_lines: List[str]) -> Optional[Dict[str, str]]:
        """解析 dbus-monitor 的 Notify 方法调用块"""
        string_values = []
        for line in block_lines:
            value = self._extract_string_value(line)
            if value is not None:
                string_values.append(value)

        if len(string_values) < 5:
            logger.debug("Skip incomplete Notify block: %s", block_lines)
            return None

        return {
            "app_name": string_values[0],
            "summary": string_values[2],
            "body": string_values[3],
        }

    def _monitor_notifications(self):
        """通过 dbus-monitor 监听通知方法调用"""
        command = [
            "dbus-monitor",
            "--session",
            "interface='org.freedesktop.Notifications',member='Notify'",
        ]

        try:
            self._monitor_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            logger.error(
                "dbus-monitor command not found; please install dbus-x11 or dbus tools"
            )
            self.running = False
            return
        except Exception as e:
            logger.error(f"Failed to start dbus-monitor: {e}")
            self.running = False
            return

        logger.info("DBus notification monitor started via dbus-monitor")

        current_block: List[str] = []
        in_notify_block = False

        assert self._monitor_process.stdout is not None
        for raw_line in self._monitor_process.stdout:
            if not self.running:
                break

            line = raw_line.rstrip("\n")
            if "member=Notify" in line and "org.freedesktop.Notifications" in line:
                in_notify_block = True
                current_block = [line]
                continue

            if in_notify_block:
                if line.strip() == "":
                    payload = self._parse_notify_block(current_block)
                    if payload:
                        self._handle_notification_payload(
                            payload["app_name"], payload["summary"], payload["body"]
                        )
                    current_block = []
                    in_notify_block = False
                    continue

                current_block.append(line)

        logger.info("DBus monitor loop exited")

    def _setup_dbus_monitor(self):
        """
        设置DBus监控
        """
        try:
            DBusGMainLoop(set_as_default=True)
            self.session_bus = dbus.SessionBus()
            logger.info("DBus session bus connected")
            return True

        except Exception as e:
            logger.error(f"Failed to setup DBus monitor: {e}")
            return False

    def start(self):
        """启动监听器"""
        if self.running:
            return

        self.running = True

        if not self._setup_dbus_monitor():
            self.running = False
            return False

        logger.info("WeChat DBus listener started")

        self._monitor_thread = threading.Thread(
            target=self._monitor_notifications,
            name="wechat-dbus-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

        self.loop = GLib.MainLoop()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            self.stop()

        return True

    def stop(self):
        """停止监听器"""
        self.running = False
        if self._monitor_process and self._monitor_process.poll() is None:
            self._monitor_process.terminate()
        if self.loop:
            self.loop.quit()
        logger.info("WeChat DBus listener stopped")

    def run_async(self):
        """在后台线程中运行"""
        import threading

        thread = threading.Thread(target=self.start)
        thread.daemon = True
        thread.start()
        return thread


class MockDBusListener:
    """
    模拟DBus监听器（用于测试或非Linux环境）
    """

    def __init__(
        self, callback: Optional[Callable[[PaymentNotification], None]] = None
    ):
        self.callback = callback
        self.running = False

    def start(self):
        """模拟启动"""
        self.running = True
        logger.info("Mock DBus listener started (simulation mode)")

        # 模拟一些测试数据
        import time

        while self.running:
            time.sleep(1)

    def stop(self):
        self.running = False

    def simulate_payment(self, amount: float, payer: str):
        """模拟支付通知（用于测试）"""
        if self.callback:
            payment = PaymentNotification(
                amount=amount,
                payer=payer,
                timestamp=datetime.now(),
                raw_text=f"收到 {payer} 付款 {amount} 元",
                source="mock",
            )
            self.callback(payment)


def create_listener(
    callback: Callable[[PaymentNotification], None], use_mock: bool = False
) -> WeChatDBusListener:
    """
    创建合适的监听器实例
    """
    if use_mock or not HAS_DBUS:
        return MockDBusListener(callback)
    return WeChatDBusListener(callback)


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)

    def on_payment(payment):
        print(f"\n=== 收到支付 ===")
        print(f"金额: {payment.amount} 元")
        print(f"付款人: {payment.payer}")
        print(f"时间: {payment.timestamp}")
        print(f"来源: {payment.source}")
        print(f"原始文本: {payment.raw_text}")
        print("================\n")

    listener = create_listener(on_payment)

    print("启动微信支付监听器...")
    print("请确保微信Linux版正在运行并会产生桌面通知")
    print("按 Ctrl+C 停止")

    try:
        listener.start()
    except KeyboardInterrupt:
        listener.stop()
