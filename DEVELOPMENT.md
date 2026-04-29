# Linux 微信支付回调系统开发文档

## 概述

本文档记录 Linux 微信支付回调系统的开发过程、问题修复和优化内容。

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Linux WeChat 容器                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │ session_db   │───▶│  db_monitor  │───▶│  Payment Detection   │  │
│  │ (PayMsg表)   │    │  监听支付消息  │    │  + Cross Dedup        │  │
│  └──────────────┘    └──────────────┘    └──────────┬───────────┘  │
│                                                      │               │
│  ┌──────────────┐    ┌──────────────┐             │               │
│  │ biz_message  │───▶│  (已禁用)     │             │               │
│  │ _db          │    │              │             │               │
│  └──────────────┘    └──────────────┘             ▼               │
│                                          ┌──────────────────────┐   │
│  ┌──────────────┐                        │  callback_sender    │   │
│  │ DBus Listener│───────────────────────▶│  + Signature Gen    │   │
│  │ (备用检测)    │                        └──────────┬───────────┘   │
│  └──────────────┘                                   │               │
│                                                      ▼               │
│                                           ┌──────────────────────┐   │
│                                           │  HTTP POST Callback  │   │
│                                           └──────────┬───────────┘   │
│                                                      │               │
└──────────────────────────────────────────────────────┼───────────────┘
                                                       │
                                                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Message Bot 服务器                               │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │                    /api/payment/notify/linux_wechat            │ │
│  │  1. 验证签名 (HMAC-SHA256)                                      │ │
│  │  2. 客户端去重 (amount:timestamp:source)                        │ │
│  │  3. 订单匹配 (按金额唯一匹配)                                     │ │
│  │  4. 更新订单状态 -> paid                                        │ │
│  │  5. 触发支付成功事件                                             │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 支付检测流程

```
用户支付 ──▶ 微信数据库变更 ──▶ db_monitor 检测 ──▶ 跨源去重
                                        │                    │
                                        │ 通过                │ 拦截
                                        ▼                    ▼
                              callback_sender        忽略重复
                                        │
                                        ▼
                              HTTP POST 到 Message Bot
                                        │
                                        ▼
                              linux_wechat_api.py
                                        │
                            ┌───────────┴───────────┐
                            │ 1. 验证签名            │
                            │ 2. 客户端去重          │
                            │ 3. 订单匹配            │
                            │ 4. 更新状态            │
                            └───────────┬───────────┘
                                        │
                                        ▼
                              支付成功事件触发
```

---

## 2026-04-29 优化记录

### 问题描述

1. **SESSION_DB_PATH 为空**：config.yaml 中 session_db_path 未正确加载
2. **环境变量不生效**：CALLBACK_URL 等环境变量无法覆盖配置
3. **签名验证失败**：Linux 端与 Message Bot 端签名算法不一致
4. **检测延迟高**：支付完成后约 24 秒才收到回调
5. **重复回调**：同一笔支付触发多次回调和孤儿记录

### 修复内容

#### 1. 配置加载修复 (config.py)

**问题**：环境变量无法覆盖配置文件

**修复**：添加 `_override_from_env()` 方法，从环境变量读取配置

**完整代码**：

```python
# config.py - ConfigManager 类中新增方法
def _override_from_env(self):
    """从环境变量覆盖配置"""
    # 回调URL
    if call_back_url := os.environ.get("CALLBACK_URL"):
        self.config.callback.url = call_back_url

    # 回调密钥
    if secret_key := os.environ.get("CALLBACK_SECRET_KEY"):
        self.config.callback.secret_key = secret_key

    # Session DB 路径
    if session_db_path := os.environ.get("SESSION_DB_PATH"):
        self.config.database_monitor.session_db_path = session_db_path

    # 解密项目目录
    if decrypt_dir := os.environ.get("DECRYPT_PROJECT_DIR"):
        self.config.database_monitor.decrypt_project_dir = decrypt_dir

    # 轮询间隔
    if poll_interval := os.environ.get("POLL_INTERVAL"):
        try:
            self.config.database_monitor.poll_interval = float(poll_interval)
        except ValueError:
            pass

    logger.info("Configuration overridden from environment variables")
```

**调用位置**：在 `load()` 方法末尾调用

```python
def load(cls, config_file: Optional[str] = None) -> "ConfigManager":
    """加载配置"""
    instance = cls()
    # ... 加载配置文件的代码 ...

    # 新增：从环境变量覆盖
    instance._override_from_env()

    return instance
```

#### 2. 签名算法对齐 (callback_server.py)

**问题**：Linux 端与 Message Bot 端签名算法不一致导致验证失败

**修复**：对齐以下细节：
- 使用 HMAC-SHA256
- 按 key 排序后拼接
- 排除 None 值
- raw_data 使用 JSON 序列化字符串

**完整代码**：

```python
# callback_server.py - CallbackSender 类

def _generate_signature(self, data: dict, secret_key: str) -> str:
    """
    生成签名

    签名算法：
    1. 排除 signature 字段
    2. 按 key 字母排序
    3. 排除 value 为 None 的字段
    4. 格式：key1=value1&key2=value2&...
    5. 使用 HMAC-SHA256
    """
    # 排除 signature 字段
    data_copy = {k: v for k, v in data.items() if k != "signature"}

    # 处理 raw_data - 确保是 JSON 字符串
    if "raw_data" in data_copy and isinstance(data_copy["raw_data"], dict):
        data_copy["raw_data"] = json.dumps(
            data_copy["raw_data"], ensure_ascii=False
        )

    # 按 key 排序并拼接
    sign_str = "&".join(
        f"{k}={v}" for k, v in sorted(data_copy.items()) if v is not None
    )

    # 生成 HMAC-SHA256 签名
    signature = hmac.new(
        secret_key.encode(),
        sign_str.encode(),
        hashlib.sha256
    ).hexdigest()

    return signature

def _prepare_payment_data(self, payment: PaymentCallback) -> dict:
    """准备支付回调数据"""
    data = {
        "order_id": payment.order_id,
        "amount": payment.amount,
        "payer": payment.payer,
        "timestamp": payment.timestamp,
        "payment_method": payment.payment_method or "wechat",
        "source": payment.source,
        "raw_data": payment.raw_text,
    }

    # 添加签名
    if self.secret_key:
        data["signature"] = self._generate_signature(data, self.secret_key)

    return data
```

#### 3. 跨源去重 (main.py)

**问题**：session_db 和 biz_message_db 都检测到同一笔支付，触发多次回调

**修复**：添加跨源去重 key，使用 `金额:时间戳` 作为全局唯一标识

**完整代码**：

```python
# main.py - WeChatPayCallbackSystem 类

def __init__(self, config_path: str = "config.yaml"):
    # ... 其他初始化 ...

    # 去重缓存 {key: timestamp}
    self._recent_payments: Dict[str, float] = {}
    # 去重缓存有效期（秒）
    self._dedup_cache_ttl = 1800  # 30分钟

def _on_payment_from_database(self, result: DatabasePaymentResult):
    """处理数据库监听到的支付摘要"""
    logger.info(
        "Payment detected via DB: %s元 from %s, username=%s, summary=%s",
        result.amount,
        result.payer,
        result.username,
        result.summary,
    )

    # ========== 跨源去重（关键修复）==========
    # 使用 金额+时间戳 作为全局去重key
    # 避免 session_db 和 biz_message_db 重复发送
    cross_source_key = f"cross::{result.amount:.2f}::{result.last_timestamp}"
    if cross_source_key in self._recent_payments:
        logger.info(
            "Cross-source duplicate payment ignored: %s元, source=%s, timestamp=%s",
            result.amount,
            result.source,
            result.last_timestamp,
        )
        return

    # 本源去重（同一源头的重复检测）
    dedup_key = (
        f"{result.source}::{result.username}::{result.last_timestamp}::{result.amount:.2f}"
    )
    if dedup_key in self._recent_payments:
        logger.info("Duplicate database payment ignored: %s", dedup_key)
        return

    # 记录到去重缓存
    current_time = time.time()
    self._recent_payments[cross_source_key] = current_time
    self._recent_payments[dedup_key] = current_time
    self._cleanup_recent_payments()
    # ========== 去重结束 ==========

    # 关联订单
    order_id = self._match_order(result.amount, result.payer)

    # 创建PaymentNotification对象
    notification = PaymentNotification(
        amount=result.amount,
        payer=result.payer,
        timestamp=result.timestamp,
        raw_text=json.dumps(result.to_dict(), ensure_ascii=False),
        source=result.source,
    )
    notification.order_id = order_id

    # 发送回调
    self._send_callback(notification)
    self.stats["payments_detected"] += 1

def _cleanup_recent_payments(self):
    """清理过期去重缓存"""
    current_time = time.time()
    self._recent_payments = {
        k: v for k, v in self._recent_payments.items()
        if current_time - v < self._dedup_cache_ttl
    }
```

#### 4. 客户端去重 (linux_wechat_api.py)

**问题**：Message Bot 端重复处理同一回调

**修复**：添加基于 amount:timestamp:source 的去重缓存

**完整代码**：

```python
# web/linux_wechat_api.py - 文件开头

from flask import request, jsonify, Blueprint
from datetime import datetime
import logging
import json
import hmac
import hashlib
import uuid
import time

logger = logging.getLogger(__name__)

# 创建蓝图
linux_wechat_bp = Blueprint("linux_wechat_payment", __name__, url_prefix="/api/payment")

# ========== 客户端去重缓存 ==========
# 格式: {dedup_key: timestamp}
# dedup_key = f"{amount}:{timestamp}:{source}"
_dedup_cache: dict = {}
DEDUP_KEY_SECONDS = 60
# ====================================


def verify_linux_wechat_signature(data: dict, secret_key: str) -> bool:
    """验证Linux微信支付回调签名"""
    if not secret_key:
        return True  # 未配置密钥时跳过验证

    signature = data.pop("signature", "")

    # 按key排序并拼接成字符串
    sign_str = "&".join(f"{k}={v}" for k, v in sorted(data.items()) if v is not None)

    expected = hmac.new(
        secret_key.encode(), sign_str.encode(), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


@linux_wechat_bp.route("/notify/linux_wechat", methods=["POST"])
def handle_linux_wechat_payment():
    """处理Linux微信支付回调"""
    try:
        # 获取回调数据
        if request.is_json:
            notify_data = request.get_json(silent=True) or {}
        else:
            notify_data = request.form.to_dict()

        logger.info(f"📨 收到Linux微信支付通知: {notify_data}")

        # ========== 去重检查（关键修复）==========
        dedup_key = (
            f"{notify_data.get('amount', '')}:"
            f"{notify_data.get('timestamp', '')}:"
            f"{notify_data.get('source', '')}"
        )
        current_time = time.time()

        # 检查是否重复
        if dedup_key in _dedup_cache:
            if current_time - _dedup_cache[dedup_key] < DEDUP_KEY_SECONDS:
                logger.warning(f"⚠️ 重复回调已忽略: {dedup_key}")
                return "success"

        # 记录到缓存
        _dedup_cache[dedup_key] = current_time

        # 清理过期缓存
        expired_keys = [
            k for k, v in _dedup_cache.items()
            if current_time - v >= DEDUP_KEY_SECONDS
        ]
        for k in expired_keys:
            del _dedup_cache[k]
        # ========== 去重结束 ==========

        # 验证签名
        from core.payment_manager import get_payment_manager
        payment_manager = get_payment_manager(data_bucket)
        config = payment_manager.get_config("linux_wechat")

        secret_key = config.get("secret_key", "")
        if secret_key and not verify_linux_wechat_signature(
            notify_data.copy(), secret_key
        ):
            logger.warning("❌ 签名验证失败")
            return "fail"

        # ... 后续处理逻辑 ...
```

#### 5. 禁用 message_monitor

**问题**：message_monitor 与 session_db 功能重叠，导致重复检测

**修复**：禁用 message_monitor，只使用 session_db 检测支付

```yaml
# config/vnc/config.yaml
message_monitor:
  enabled: false

database_monitor:
  enabled: true
```

### 优化配置

#### db_monitor.py 参数优化

**文件位置**：`linux_wechat_pay/db_monitor.py`

**修改位置**：WeChatSessionDatabaseMonitor 类的 `__init__` 方法

```python
# 原始配置
self._refresh_debounce_seconds = 0.3
self._refresh_min_interval_seconds = 0.8
self._sleep_interval = min(self.poll_interval, 0.5)

# 优化后配置
self._refresh_debounce_seconds = 0.2
self._refresh_min_interval_seconds = 0.5
self._sleep_interval = min(self.poll_interval, 0.3)
```

**参数说明**：
- `_refresh_debounce_seconds`：检测到变化后的防抖等待时间
- `_refresh_min_interval_seconds`：刷新操作的最小间隔
- `_sleep_interval`：每次轮询后的休眠时间

#### config.yaml 参数优化

**文件位置**：`linux_wechat_pay/config/vnc/config.yaml`

```yaml
# 数据库监听配置
database_monitor:
  enabled: true
  poll_interval: 1.0  # 从 2.0s 优化到 1.0s

# 消息数据库监听（已禁用）
message_monitor:
  enabled: false

# 回调配置
callback:
  url: "${CALLBACK_URL}"
  secret_key: "${CALLBACK_SECRET_KEY}"
  timeout: 30
  max_retries: 3
  retry_delay: 5
```

#### 参数优化对照表

| 参数 | 优化前 | 优化后 | 效果 |
|------|--------|--------|------|
| database_monitor.poll_interval | 2.0s | 1.0s | 轮询更频繁 |
| _refresh_debounce_seconds | 0.3s | 0.2s | 更早触发检测 |
| _refresh_min_interval_seconds | 0.8s | 0.5s | 减少刷新间隔 |
| _sleep_interval | 0.5s | 0.3s | 减少休眠时间 |

### 最终效果

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 延迟 | ~24秒 | ~23秒 |
| 回调发送 | 多次 | 1次 |
| 回调处理 | 4次 | 2次 |
| 孤儿记录 | 3个 | 0个 |
| 支付成功 | ✅ | ✅ |

---

## 文件变更清单

### 修改的文件

1. **config.py**
   - 添加 `_override_from_env()` 方法（完整实现）
   - 添加 `load_config()` 兼容函数
   - 添加 `keys_file` 和 `config_file` 配置

2. **callback_server.py**
   - 修复 `_generate_signature()` 算法（完整实现）
   - 添加 `_prepare_payment_data()` 方法
   - 确保 raw_data 使用 JSON 字符串

3. **main.py**
   - 添加跨源去重逻辑（完整实现）
   - 添加 `_cleanup_recent_payments()` 方法
   - 更新 `_recent_payments` 缓存结构

4. **web/linux_wechat_api.py**
   - 添加客户端去重缓存（完整实现）
   - 添加 `_dedup_cache` 全局变量
   - 添加 DEDUP_KEY_SECONDS 常量
   - 在回调处理开头添加去重检查

5. **config/vnc/config.yaml**
   - 禁用 message_monitor
   - 优化 database_monitor.poll_interval
   - 优化 callback 重试配置

6. **db_monitor.py**
   - 优化轮询参数（_refresh_debounce_seconds 等）

7. **wechat-decrypt/key_utils.py**
   - 复制到 /app 目录解决模块导入问题

### 容器部署文件

将以下文件复制到容器 `/app` 目录：
- `config.py`
- `callback_server.py`
- `main.py`
- `db_monitor.py`
- `config.yaml`
- `wechat-decrypt/key_utils.py`

---

## 待优化项

1. **进一步降低延迟**：当前 23 秒，可通过更短的轮询间隔优化
2. **biz_message_db 检测**：修复后可作为提前检测源（比 session_db 快）
3. **message_monitor 重构**：分离支付检测和消息转发功能

---

## 数据结构参考

### DatabasePaymentResult

```python
# db_monitor.py
@dataclass
class DatabasePaymentResult:
    """数据库支付监听结果"""
    amount: float
    payer: str
    timestamp: str
    username: str
    summary: str
    last_timestamp: int
    last_msg_type: int
    last_msg_sub_type: int
    sender_name: str
    source: str  # "session_db" 或 "biz_message_db"

    def to_dict(self) -> dict:
        return {
            "amount": self.amount,
            "payer": self.payer,
            "timestamp": self.timestamp,
            "username": self.username,
            "summary": self.summary,
            "last_timestamp": self.last_timestamp,
            "last_msg_type": self.last_msg_type,
            "last_msg_sub_type": self.last_msg_sub_type,
            "sender_name": self.sender_name,
            "source": self.source,
        }
```

### PaymentNotification

```python
# main.py
@dataclass
class PaymentNotification:
    """支付通知"""
    amount: float
    payer: str
    timestamp: str
    raw_text: str
    source: str
    order_id: Optional[str] = None
```

### PaymentCallback

```python
# callback_server.py
@dataclass
class PaymentCallback:
    """支付回调数据"""
    order_id: Optional[str]
    amount: float
    payer: str
    timestamp: str
    payment_method: str = "wechat"
    source: str = "unknown"
    raw_text: str = ""
```

---

## 部署命令

```bash
# 复制文件到容器
docker cp "D:\Message Bot\linux_wechat_pay\main.py" linux-wechat-vnc:/app/main.py
docker cp "D:\Message Bot\linux_wechat_pay\config.py" linux-wechat-vnc:/app/config.py
docker cp "D:\Message Bot\linux_wechat_pay\callback_server.py" linux-wechat-vnc:/app/callback_server.py
docker cp "D:\Message Bot\linux_wechat_pay\db_monitor.py" linux-wechat-vnc:/app/db_monitor.py
docker cp "D:\Message Bot\linux_wechat_pay\config\vnc\config.yaml" linux-wechat-vnc:/app/config.yaml
docker cp "D:\Message Bot\linux_wechat_pay\wechat-decrypt\key_utils.py" linux-wechat-vnc:/app/key_utils.py

# 重启服务
docker exec linux-wechat-vnc bash -c "pkill -9 -f 'python3 /app/main.py'; sleep 2; cd /app && python3 main.py -c config.yaml > /tmp/wechat-pay.log 2>&1 &"
```

---

## 调试命令

```bash
# 查看服务日志
docker exec linux-wechat-vnc bash -c "tail -20 /tmp/wechat-pay.log"

# 检查进程
docker exec linux-wechat-vnc bash -c "ps aux | grep main.py"

# 检查回调发送记录
docker exec linux-wechat-vnc bash -c "grep -E 'DETECTED|Callback sent' /tmp/wechat-pay.log"

# 查看完整错误日志
docker exec linux-wechat-vnc bash -c "cat /tmp/wechat-pay.log | grep -i error"

# 检查支付检测详情
docker exec linux-wechat-vnc bash -c "grep -E 'Payment detected|biz_message_db|session_db' /tmp/wechat-pay.log"
```

---

## 常见问题排查

### 1. 端口占用问题

**症状**：启动时提示 "Port 8888 is in use"

**排查**：
```bash
# 检查端口占用
docker exec linux-wechat-vnc bash -c "lsof -i :8888"

# 杀掉占用进程
docker exec linux-wechat-vnc bash -c "pkill -9 -f 'python3.*main.py'"
```

### 2. 签名验证失败

**症状**：Message Bot 日志显示 "签名验证失败"

**排查**：
```bash
# 检查两端签名算法是否一致
docker exec linux-wechat-vnc bash -c "grep -A10 '_generate_signature' /app/callback_server.py"

# 检查密钥配置
docker exec linux-wechat-vnc bash -c "echo \$CALLBACK_SECRET_KEY"
```

### 3. 重复回调问题

**症状**：同一笔支付收到多次回调

**排查**：
```bash
# 检查跨源去重是否生效
docker exec linux-wechat-vnc bash -c "grep -E 'Cross-source duplicate|duplicate' /tmp/wechat-pay.log"

# 检查客户端去重日志
grep "重复回调已忽略" bot.log
```

### 4. 检测延迟过高

**症状**：支付完成后超过 20 秒才收到回调

**排查**：
```bash
# 检查轮询间隔配置
docker exec linux-wechat-vnc bash -c "grep -E 'poll_interval|interval' /app/config.yaml"

# 检查检测源
docker exec linux-wechat-vnc bash -c "grep -E 'DETECTED' /tmp/wechat-pay.log"
```

### 5. 模块导入错误

**症状**：`ModuleNotFoundError: No module named 'key_utils'`

**解决**：
```bash
# 复制 key_utils.py 到 /app
docker cp "D:\Message Bot\linux_wechat_pay\wechat-decrypt\key_utils.py" linux-wechat-vnc:/app/
```

### 6. decrypt_db.py 运行失败

**症状**：`KeyError: 'keys_file'`

**解决**：已在 config.py 的 `load_config()` 中添加默认值