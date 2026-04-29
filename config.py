#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Linux微信支付回调系统 - 配置管理
"""

import os
import json
import yaml
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


def load_config() -> Dict[str, Any]:
    """兼容旧版本的 decrypt_db.py"""
    project_dir = os.environ.get("DECRYPT_PROJECT_DIR", "/root/wechat-decrypt")
    return {
        "db_dir": project_dir,
        "decrypted_dir": os.path.join(project_dir, "decrypted"),
        "keys_file": os.path.join(project_dir, "all_keys.json"),
        "config_file": os.path.join(project_dir, "config.json"),
    }


@dataclass
class CallbackConfig:
    """回调配置"""

    url: str = ""
    secret_key: Optional[str] = None
    timeout: int = 10
    retry_count: int = 3
    retry_delay: float = 1.0
    enabled: bool = True


@dataclass
class DBusConfig:
    """DBus监听配置"""

    enabled: bool = True
    monitor_duration: Optional[int] = None  # None表示一直运行
    filter_keywords: List[str] = field(
        default_factory=lambda: ["收款", "支付", "赞赏", "收到", "入账", "转账"]
    )


@dataclass
class OCRConfig:
    """OCR扫描配置"""

    enabled: bool = True
    engine: str = "auto"  # auto, tesseract, easyocr
    language: str = "chi_sim+eng"
    confidence_threshold: float = 0.6
    scan_interval: float = 2.0
    scan_region: Optional[List[int]] = None  # [x, y, width, height]
    fallback_mode: bool = True  # 当DBus不可用时作为主要检测方式


@dataclass
class DatabaseMonitorConfig:
    """数据库监听配置"""

    enabled: bool = False
    decrypt_project_dir: str = "/root/wechat-decrypt"
    session_db_path: str = "/root/wechat-decrypt/decrypted/session/session.db"
    poll_interval: float = 2.0
    summary_keywords: List[str] = field(
        default_factory=lambda: ["收款", "到账", "二维码", "赞赏", "微信支付"]
    )
    trusted_usernames: List[str] = field(
        default_factory=lambda: ["brandservicesessionholder"]
    )
    trusted_sender_names: List[str] = field(default_factory=lambda: ["微信收款助手"])


@dataclass
class MessageMonitorConfig:
    """普通消息监听配置"""

    enabled: bool = False
    decrypt_project_dir: str = "/root/wechat-decrypt"
    callback_url: str = ""
    secret_key: Optional[str] = None
    poll_interval: float = 2.0
    include_message_types: List[str] = field(
        default_factory=lambda: ["text", "image", "voice", "video"]
    )


@dataclass
class ServerConfig:
    """本地服务器配置"""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8888
    websocket_port: int = 8889
    max_records: int = 1000
    enable_webhook: bool = True


@dataclass
class WeChatPayConfig:
    """主配置类"""

    version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"
    data_dir: str = "./data"

    callback: CallbackConfig = field(default_factory=CallbackConfig)
    dbus: DBusConfig = field(default_factory=DBusConfig)
    ocr: OCRConfig = field(default_factory=OCRConfig)
    database_monitor: DatabaseMonitorConfig = field(
        default_factory=DatabaseMonitorConfig
    )
    message_monitor: MessageMonitorConfig = field(default_factory=MessageMonitorConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    # 订单关联配置
    order_config: Dict[str, Any] = field(
        default_factory=lambda: {
            "enable_auto_order_id": True,
            "order_id_prefix": "WXP",
            "amount_variation_step": 0.01,  # 金额微调步长（用于区分并发订单）
            "amount_variation_max": 0.99,  # 最大微调金额
        }
    )

    # 通知配置
    notification: Dict[str, Any] = field(
        default_factory=lambda: {
            "enable_sound": False,
            "enable_desktop_notification": True,
            "sound_file": None,
            "on_payment_success": "支付成功通知",
        }
    )


class ConfigManager:
    """
    配置管理器
    负责加载、保存和管理配置
    """

    DEFAULT_CONFIG_PATHS = [
        "./config.yaml",
        "./config.yml",
        "./config.json",
        "~/.wechat_pay_callback/config.yaml",
        "/etc/wechat_pay_callback/config.yaml",
    ]

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        self.config = WeChatPayConfig()
        self._loaded_path: Optional[str] = None

    def _resolve_path(self, path: str) -> Optional[Path]:
        """解析路径（支持~扩展）"""
        expanded = os.path.expanduser(path)
        p = Path(expanded)
        if p.exists():
            return p
        return None

    def find_config_file(self) -> Optional[Path]:
        """查找配置文件"""
        # 首先检查指定的路径
        if self.config_path:
            p = self._resolve_path(self.config_path)
            if p:
                return p

        # 然后搜索默认路径
        for path in self.DEFAULT_CONFIG_PATHS:
            p = self._resolve_path(path)
            if p:
                return p

        return None

    def load(self, path: Optional[str] = None) -> WeChatPayConfig:
        """
        加载配置文件

        Args:
            path: 配置文件路径，None则自动查找

        Returns:
            配置对象
        """
        if path:
            config_file = self._resolve_path(path)
        else:
            config_file = self.find_config_file()

        if not config_file:
            logger.info("No config file found, using default configuration")
            return self.config

        try:
            with open(config_file, "r", encoding="utf-8") as f:
                if config_file.suffix in [".yaml", ".yml"]:
                    data = yaml.safe_load(f)
                else:
                    data = json.load(f)

            # 更新配置
            self._update_from_dict(data)
            self._loaded_path = str(config_file)

            logger.info(f"Configuration loaded from {config_file}")
            return self.config

        except Exception as e:
            logger.error(f"Failed to load config from {config_file}: {e}")
            return self.config

    def save(self, path: Optional[str] = None) -> bool:
        """
        保存配置到文件

        Args:
            path: 保存路径，None则使用加载时的路径或默认路径

        Returns:
            是否保存成功
        """
        if path:
            save_path = Path(path)
        elif self._loaded_path:
            save_path = Path(self._loaded_path)
        else:
            save_path = Path("./config.yaml")

        try:
            # 确保目录存在
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # 转换为字典
            data = self._to_dict()

            with open(save_path, "w", encoding="utf-8") as f:
                if save_path.suffix in [".yaml", ".yml"]:
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
                else:
                    json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info(f"Configuration saved to {save_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False

    def _update_from_dict(self, data: Dict[str, Any]):
        """从字典更新配置"""
        if not data:
            return

        # 更新基本配置
        if "debug" in data:
            self.config.debug = data["debug"]
        if "log_level" in data:
            self.config.log_level = data["log_level"]
        if "data_dir" in data:
            self.config.data_dir = data["data_dir"]

        # 更新回调配置
        if "callback" in data:
            cb = data["callback"]
            self.config.callback.url = cb.get("url", self.config.callback.url)
            self.config.callback.secret_key = cb.get("secret_key")
            self.config.callback.timeout = cb.get("timeout", 10)
            self.config.callback.retry_count = cb.get("retry_count", 3)
            self.config.callback.enabled = cb.get("enabled", True)

        # 更新DBus配置
        if "dbus" in data:
            db = data["dbus"]
            self.config.dbus.enabled = db.get("enabled", True)
            self.config.dbus.monitor_duration = db.get("monitor_duration")
            self.config.dbus.filter_keywords = db.get(
                "filter_keywords", ["收款", "支付"]
            )

        # 更新OCR配置
        if "ocr" in data:
            oc = data["ocr"]
            self.config.ocr.enabled = oc.get("enabled", True)
            self.config.ocr.engine = oc.get("engine", "auto")
            self.config.ocr.language = oc.get("language", "chi_sim+eng")
            self.config.ocr.confidence_threshold = oc.get("confidence_threshold", 0.6)
            self.config.ocr.scan_interval = oc.get("scan_interval", 2.0)
            self.config.ocr.scan_region = oc.get("scan_region")
            self.config.ocr.fallback_mode = oc.get("fallback_mode", True)

        # 更新数据库监听配置
        if "database_monitor" in data:
            dm = data["database_monitor"]
            self.config.database_monitor.enabled = dm.get("enabled", False)
            self.config.database_monitor.decrypt_project_dir = dm.get(
                "decrypt_project_dir", self.config.database_monitor.decrypt_project_dir
            )
            self.config.database_monitor.session_db_path = dm.get(
                "session_db_path", self.config.database_monitor.session_db_path
            )
            self.config.database_monitor.poll_interval = dm.get("poll_interval", 2.0)
            self.config.database_monitor.summary_keywords = dm.get(
                "summary_keywords", self.config.database_monitor.summary_keywords
            )
            self.config.database_monitor.trusted_usernames = dm.get(
                "trusted_usernames", self.config.database_monitor.trusted_usernames
            )
            self.config.database_monitor.trusted_sender_names = dm.get(
                "trusted_sender_names",
                self.config.database_monitor.trusted_sender_names,
            )

        # 更新普通消息监听配置
        if "message_monitor" in data:
            mm = data["message_monitor"]
            self.config.message_monitor.enabled = mm.get("enabled", False)
            self.config.message_monitor.decrypt_project_dir = mm.get(
                "decrypt_project_dir", self.config.message_monitor.decrypt_project_dir
            )
            self.config.message_monitor.callback_url = mm.get(
                "callback_url", self.config.message_monitor.callback_url
            )
            self.config.message_monitor.secret_key = mm.get(
                "secret_key", self.config.message_monitor.secret_key
            )
            self.config.message_monitor.poll_interval = mm.get("poll_interval", 2.0)
            self.config.message_monitor.include_message_types = mm.get(
                "include_message_types",
                self.config.message_monitor.include_message_types,
            )

        # 更新服务器配置
        if "server" in data:
            sv = data["server"]
            self.config.server.enabled = sv.get("enabled", True)
            self.config.server.host = sv.get("host", "0.0.0.0")
            self.config.server.port = sv.get("port", 8888)
            self.config.server.max_records = sv.get("max_records", 1000)

        # 更新订单配置
        if "order_config" in data:
            self.config.order_config.update(data["order_config"])

        # 更新通知配置
        if "notification" in data:
            self.config.notification.update(data["notification"])

        # 环境变量覆盖配置（支持 Docker 环境变量注入）
        self._override_from_env()

    def _override_from_env(self):
        """从环境变量覆盖配置，支持 Docker 环境变量注入和自动检测"""
        import os
        from pathlib import Path

        # 回调配置
        if os.environ.get("CALLBACK_URL"):
            self.config.callback.url = os.environ.get("CALLBACK_URL")
        if os.environ.get("CALLBACK_SECRET_KEY"):
            self.config.callback.secret_key = os.environ.get("CALLBACK_SECRET_KEY")

        # 数据库监听配置 - 支持环境变量或自动检测
        if os.environ.get("DECRYPT_PROJECT_DIR"):
            self.config.database_monitor.decrypt_project_dir = os.environ.get("DECRYPT_PROJECT_DIR")
        else:
            # 自动检测解密项目目录
            default_decrypt_dir = Path("/root/wechat-decrypt")
            if default_decrypt_dir.exists():
                self.config.database_monitor.decrypt_project_dir = str(default_decrypt_dir)

        if os.environ.get("SESSION_DB_PATH"):
            self.config.database_monitor.session_db_path = os.environ.get("SESSION_DB_PATH")
        else:
            # 自动检测 session.db 路径
            decrypt_dir = self.config.database_monitor.decrypt_project_dir
            if decrypt_dir:
                session_db = Path(decrypt_dir) / "decrypted" / "session" / "session.db"
                if session_db.exists():
                    self.config.database_monitor.session_db_path = str(session_db)
                else:
                    # 尝试常见路径
                    for path in [
                        Path("/root/wechat-decrypt/decrypted/session/session.db"),
                        Path(decrypt_dir).parent / "decrypted" / "session" / "session.db"
                    ]:
                        if path.exists():
                            self.config.database_monitor.session_db_path = str(path)
                            break

        # 日志级别覆盖
        if os.environ.get("LOG_LEVEL"):
            self.config.log_level = os.environ.get("LOG_LEVEL")

    def _to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "version": self.config.version,
            "debug": self.config.debug,
            "log_level": self.config.log_level,
            "data_dir": self.config.data_dir,
            "callback": {
                "url": self.config.callback.url,
                "secret_key": self.config.callback.secret_key,
                "timeout": self.config.callback.timeout,
                "retry_count": self.config.callback.retry_count,
                "retry_delay": self.config.callback.retry_delay,
                "enabled": self.config.callback.enabled,
            },
            "dbus": {
                "enabled": self.config.dbus.enabled,
                "monitor_duration": self.config.dbus.monitor_duration,
                "filter_keywords": self.config.dbus.filter_keywords,
            },
            "ocr": {
                "enabled": self.config.ocr.enabled,
                "engine": self.config.ocr.engine,
                "language": self.config.ocr.language,
                "confidence_threshold": self.config.ocr.confidence_threshold,
                "scan_interval": self.config.ocr.scan_interval,
                "scan_region": self.config.ocr.scan_region,
                "fallback_mode": self.config.ocr.fallback_mode,
            },
            "database_monitor": {
                "enabled": self.config.database_monitor.enabled,
                "decrypt_project_dir": self.config.database_monitor.decrypt_project_dir,
                "session_db_path": self.config.database_monitor.session_db_path,
                "poll_interval": self.config.database_monitor.poll_interval,
                "summary_keywords": self.config.database_monitor.summary_keywords,
                "trusted_usernames": self.config.database_monitor.trusted_usernames,
                "trusted_sender_names": self.config.database_monitor.trusted_sender_names,
            },
            "message_monitor": {
                "enabled": self.config.message_monitor.enabled,
                "decrypt_project_dir": self.config.message_monitor.decrypt_project_dir,
                "callback_url": self.config.message_monitor.callback_url,
                "secret_key": self.config.message_monitor.secret_key,
                "poll_interval": self.config.message_monitor.poll_interval,
                "include_message_types": self.config.message_monitor.include_message_types,
            },
            "server": {
                "enabled": self.config.server.enabled,
                "host": self.config.server.host,
                "port": self.config.server.port,
                "websocket_port": self.config.server.websocket_port,
                "max_records": self.config.server.max_records,
                "enable_webhook": self.config.server.enable_webhook,
            },
            "order_config": self.config.order_config,
            "notification": self.config.notification,
        }

    def create_default_config(self) -> str:
        """创建默认配置文件"""
        default_yaml = """# Linux微信支付回调系统配置
version: "1.0.0"

# 调试模式
debug: false
log_level: INFO

# 数据存储目录
data_dir: "./data"

# 回调配置
callback:
  enabled: true
  url: ""  # 你的回调URL，如: https://yourdomain.com/api/payment/notify/linux_wechat
  secret_key: null  # 用于签名验证的密钥
  timeout: 10
  retry_count: 3
  retry_delay: 1.0

# DBus通知监听配置
dbus:
  enabled: true
  monitor_duration: null  # null表示一直运行
  filter_keywords:
    - "收款"
    - "支付"
    - "赞赏"
    - "收到"
    - "入账"
    - "转账"

# OCR截图识别配置
ocr:
  enabled: true
  engine: "auto"  # auto, tesseract, easyocr
  language: "chi_sim+eng"
  confidence_threshold: 0.6
  scan_interval: 2.0  # 扫描间隔（秒）
  scan_region: null  # 指定扫描区域 [x, y, width, height]，null为全屏
  fallback_mode: true  # 当DBus不可用时作为主要检测方式

# 数据库监听配置（推荐作为主检测方式）
database_monitor:
  enabled: false
  decrypt_project_dir: "/root/wechat-decrypt"
  session_db_path: "/root/wechat-decrypt/decrypted/session/session.db"
  poll_interval: 2.0
  summary_keywords:
    - "收款"
    - "到账"
    - "二维码"
    - "赞赏"
    - "微信支付"
  trusted_usernames:
    - "brandservicesessionholder"
  trusted_sender_names:
    - "微信收款助手"

# 普通消息监听配置
message_monitor:
  enabled: false
  decrypt_project_dir: "/root/wechat-decrypt"
  callback_url: "http://127.0.0.1:5000/api/adapter/linux_wechat/message"
  secret_key: null
  poll_interval: 2.0
  include_message_types:
    - "text"
    - "image"
    - "voice"
    - "video"

# 本地服务器配置
server:
  enabled: true
  host: "0.0.0.0"
  port: 8888
  websocket_port: 8889
  max_records: 1000
  enable_webhook: true

# 订单关联配置
order_config:
  enable_auto_order_id: true
  order_id_prefix: "WXP"
  amount_variation_step: 0.01  # 金额微调步长（用于区分并发订单）
  amount_variation_max: 0.99   # 最大微调金额

# 通知配置
notification:
  enable_sound: false
  enable_desktop_notification: true
  sound_file: null
  on_payment_success: "支付成功通知"
"""
        return default_yaml


# 全局配置实例
_global_config: Optional[WeChatPayConfig] = None


def get_config() -> WeChatPayConfig:
    """获取全局配置实例"""
    global _global_config
    if _global_config is None:
        manager = ConfigManager()
        _global_config = manager.load()
    return _global_config


def set_config(config: WeChatPayConfig):
    """设置全局配置实例"""
    global _global_config
    _global_config = config


def init_config(path: Optional[str] = None) -> WeChatPayConfig:
    """初始化配置"""
    manager = ConfigManager(path)
    config = manager.load()
    set_config(config)
    return config


if __name__ == "__main__":
    # 创建默认配置文件示例
    manager = ConfigManager()
    default_config = manager.create_default_config()

    print("默认配置文件内容：")
    print(default_config)

    # 保存示例
    with open("config.example.yaml", "w", encoding="utf-8") as f:
        f.write(default_config)

    print("\n已保存到 config.example.yaml")
