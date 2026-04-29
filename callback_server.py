#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP回调服务器 - 发送支付通知到用户配置的回调地址
"""
import json
import time
import hmac
import hashlib
import logging
import requests
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass, asdict
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


@dataclass
class PaymentCallback:
    """支付回调数据结构"""
    order_id: Optional[str]
    amount: float
    payer: str
    timestamp: str
    payment_method: str  # 'wechat', 'alipay', etc.
    source: str  # 'dbus_notification', 'ocr_screenshot', 'manual'
    raw_data: Dict[str, Any]
    signature: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['raw_data'] = json.dumps(data['raw_data'])
        return data


class CallbackSender:
    """
    回调发送器
    负责将支付通知发送到用户配置的回调URL
    """
    
    def __init__(self, 
                 callback_url: str,
                 secret_key: Optional[str] = None,
                 timeout: int = 10,
                 retry_count: int = 3,
                 retry_delay: float = 1.0):
        """
        初始化回调发送器
        
        Args:
            callback_url: 回调目标URL
            secret_key: 用于签名验证的密钥
            timeout: HTTP请求超时时间
            retry_count: 失败重试次数
            retry_delay: 重试间隔（秒）
        """
        self.callback_url = callback_url
        self.secret_key = secret_key
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self._success_count = 0
        self._fail_count = 0
    
    def _generate_signature(self, data: Dict[str, Any]) -> str:
        """
        生成请求签名
        使用HMAC-SHA256，与Message Bot端保持一致
        """
        if not self.secret_key:
            return ""

        # 创建签名数据副本（排除signature键）
        sign_data = {k: v for k, v in data.items() if k != 'signature'}

        # 确保raw_data是JSON字符串（与Message Bot端一致）
        if 'raw_data' in sign_data and isinstance(sign_data['raw_data'], dict):
            sign_data['raw_data'] = json.dumps(sign_data['raw_data'], ensure_ascii=False)

        # 按key排序并拼接成字符串（排除None值，与Message Bot端一致）
        sign_str = "&".join(f"{k}={v}" for k, v in sorted(sign_data.items()) if v is not None)

        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            sign_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature
    
    def _send_request(self, payment_data: PaymentCallback) -> bool:
        """
        发送HTTP回调请求
        
        Returns:
            是否发送成功
        """
        try:
            data = payment_data.to_dict()
            
            # 添加签名
            if self.secret_key:
                data['signature'] = self._generate_signature(data)
            
            headers = {
                'Content-Type': 'application/json',
                'X-Payment-Source': 'linux-wechat-pay-callback',
                'X-Timestamp': str(int(time.time()))
            }
            
            response = requests.post(
                self.callback_url,
                json=data,
                headers=headers,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                logger.info(f"Callback sent successfully to {self.callback_url}")
                self._success_count += 1
                return True
            else:
                logger.warning(f"Callback failed: HTTP {response.status_code}")
                self._fail_count += 1
                return False
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Callback request failed: {e}")
            self._fail_count += 1
            return False
    
    def send(self, payment_data: PaymentCallback) -> bool:
        """
        发送回调（带重试机制）
        
        Args:
            payment_data: 支付回调数据
            
        Returns:
            最终是否发送成功
        """
        for attempt in range(self.retry_count):
            if attempt > 0:
                logger.info(f"Retry attempt {attempt}/{self.retry_count}...")
                time.sleep(self.retry_delay * attempt)
            
            if self._send_request(payment_data):
                return True
        
        logger.error(f"Callback failed after {self.retry_count} attempts")
        return False
    
    def get_stats(self) -> Dict[str, int]:
        """获取发送统计"""
        return {
            'success': self._success_count,
            'failed': self._fail_count,
            'total': self._success_count + self._fail_count
        }


class LocalCallbackServer:
    """
    本地回调服务器
    提供HTTP接口接收查询和配置
    """
    
    def __init__(self, host: str = '0.0.0.0', port: int = 8888):
        self.host = host
        self.port = port
        self.app = None
        self.server_thread = None
        self._payments = []  # 本地存储的支付记录
        self._max_records = 1000
        self._on_payment_received: Optional[Callable] = None
    
    def init_flask(self):
        """初始化Flask应用"""
        from flask import Flask, request, jsonify
        
        self.app = Flask(__name__)
        
        @self.app.route('/health', methods=['GET'])
        def health_check():
            return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})
        
        @self.app.route('/payments', methods=['GET'])
        def list_payments():
            """获取支付记录列表"""
            limit = request.args.get('limit', 50, type=int)
            offset = request.args.get('offset', 0, type=int)
            
            payments = self._payments[offset:offset + limit]
            return jsonify({
                'total': len(self._payments),
                'payments': payments,
                'limit': limit,
                'offset': offset
            })
        
        @self.app.route('/payments/receive', methods=['POST'])
        def receive_payment():
            """接收支付通知（内部使用）"""
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
            
            # 存储记录
            self._add_payment_record(data)
            
            # 触发回调
            if self._on_payment_received:
                self._on_payment_received(data)
            
            return jsonify({'success': True, 'received_at': datetime.now().isoformat()})
        
        @self.app.route('/config', methods=['GET', 'POST'])
        def config():
            """获取/更新配置"""
            if request.method == 'GET':
                return jsonify(self._get_config())
            else:
                new_config = request.get_json()
                # TODO: 保存配置
                return jsonify({'success': True})
        
        @self.app.route('/stats', methods=['GET'])
        def stats():
            """获取统计信息"""
            return jsonify({
                'total_payments': len(self._payments),
                'today_payments': self._count_today_payments(),
                'total_amount': sum(p.get('amount', 0) for p in self._payments)
            })
    
    def _add_payment_record(self, payment: Dict[str, Any]):
        """添加支付记录"""
        payment['received_at'] = datetime.now().isoformat()
        self._payments.insert(0, payment)
        
        # 限制存储数量
        if len(self._payments) > self._max_records:
            self._payments = self._payments[:self._max_records]
    
    def _count_today_payments(self) -> int:
        """统计今日支付数量"""
        today = datetime.now().strftime('%Y-%m-%d')
        return sum(1 for p in self._payments if p.get('timestamp', '').startswith(today))
    
    def _get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return {
            'host': self.host,
            'port': self.port,
            'max_records': self._max_records
        }
    
    def set_payment_callback(self, callback: Callable[[Dict], None]):
        """设置支付接收回调"""
        self._on_payment_received = callback
    
    def start(self, blocking: bool = False):
        """启动服务器"""
        if not self.app:
            self.init_flask()
        
        import threading
        
        def run_server():
            logger.info(f"Starting local callback server on {self.host}:{self.port}")
            self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)
        
        if blocking:
            run_server()
        else:
            self.server_thread = threading.Thread(target=run_server)
            self.server_thread.daemon = True
            self.server_thread.start()
    
    def stop(self):
        """停止服务器"""
        # Flask没有简单的停止方法，需要额外处理
        logger.info("Stopping local callback server...")


class WebSocketCallbackServer:
    """
    WebSocket回调服务器（可选）
    提供实时支付通知推送
    """
    
    def __init__(self, host: str = '0.0.0.0', port: int = 8889):
        self.host = host
        self.port = port
        self.clients = set()
    
    def start(self):
        """启动WebSocket服务器"""
        # TODO: 实现WebSocket服务器
        pass
    
    def broadcast_payment(self, payment: Dict[str, Any]):
        """广播支付通知到所有客户端"""
        # TODO: 实现广播
        pass


if __name__ == "__main__":
    # 测试回调发送
    logging.basicConfig(level=logging.DEBUG)
    
    # 创建测试回调服务器
    local_server = LocalCallbackServer(port=8888)
    local_server.start(blocking=False)
    
    # 测试发送回调
    test_sender = CallbackSender(
        callback_url="http://localhost:8888/payments/receive",
        secret_key="test_key_123"
    )
    
    test_payment = PaymentCallback(
        order_id="TEST001",
        amount=30.01,
        payer="测试用户",
        timestamp=datetime.now().isoformat(),
        payment_method="wechat",
        source="dbus_notification",
        raw_data={'test': True}
    )
    
    print("发送测试回调...")
    result = test_sender.send(test_payment)
    print(f"发送结果: {result}")
    print(f"统计: {test_sender.get_stats()}")
