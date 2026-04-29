#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
订单管理模块 - 处理订单关联和金额微调
"""
import re
import json
import time
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field, asdict
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class Order:
    """订单数据模型"""
    order_id: str
    base_amount: float
    expected_amount: float
    actual_amount: Optional[float] = None
    status: str = "pending"  # pending, paid, expired, cancelled
    payer: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    paid_at: Optional[datetime] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['created_at'] = self.created_at.isoformat() if self.created_at else None
        data['paid_at'] = self.paid_at.isoformat() if self.paid_at else None
        return data


class OrderManager:
    """
    订单管理器
    负责生成订单、关联支付、处理金额微调
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.orders: Dict[str, Order] = {}
        self.pending_orders: Dict[str, Order] = {}
        self.lock = Lock()
        
        # 配置
        self.enable_auto_id = config.get('enable_auto_order_id', True)
        self.order_prefix = config.get('order_id_prefix', 'WXP')
        self.variation_step = config.get('amount_variation_step', 0.01)
        self.variation_max = config.get('amount_variation_max', 0.99)
        
        # 订单过期时间（分钟）
        self.order_expire_minutes = 30
        
        logger.info("Order manager initialized")
    
    def generate_order_id(self) -> str:
        """生成唯一订单ID"""
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        random_suffix = uuid.uuid4().hex[:6].upper()
        return f"{self.order_prefix}{timestamp}{random_suffix}"
    
    def create_order(self, 
                    base_amount: float, 
                    description: Optional[str] = None,
                    metadata: Optional[Dict[str, Any]] = None) -> Order:
        """
        创建新订单
        
        Args:
            base_amount: 基础金额（商品原价）
            description: 订单描述
            metadata: 附加元数据
            
        Returns:
            创建的订单对象
        """
        order_id = self.generate_order_id()
        
        # 计算实际应付金额（加入微调）
        expected_amount = self._calculate_expected_amount(base_amount)
        
        order = Order(
            order_id=order_id,
            base_amount=base_amount,
            expected_amount=expected_amount,
            description=description,
            metadata=metadata or {},
            status="pending"
        )
        
        with self.lock:
            self.orders[order_id] = order
            self.pending_orders[order_id] = order
        
        logger.info(f"Order created: {order_id}, base={base_amount}, expected={expected_amount}")
        return order
    
    def _calculate_expected_amount(self, base_amount: float) -> float:
        """
        计算实际应付金额（加入微调以区分并发订单）
        
        原理：通过在最后两位小数添加随机或顺序微调，使每个订单有唯一金额
        例如：30元 -> 30.01, 30.02, 30.03 ...
        """
        # 获取当前pending订单数量
        pending_count = len(self.pending_orders)
        
        # 计算微调金额（0.01, 0.02, ...）
        variation = (pending_count % int(self.variation_max / self.variation_step) + 1) * self.variation_step
        
        # 如果微调金额达到上限，从0.01重新开始
        if variation > self.variation_max:
            variation = self.variation_step
        
        expected_amount = base_amount + variation
        
        # 确保金额唯一（检查是否已有相同金额的pending订单）
        max_attempts = 100
        attempts = 0
        while self._has_duplicate_amount(expected_amount) and attempts < max_attempts:
            variation = (variation + self.variation_step) % (self.variation_max + self.variation_step)
            if variation == 0:
                variation = self.variation_step
            expected_amount = base_amount + variation
            attempts += 1
        
        return round(expected_amount, 2)
    
    def _has_duplicate_amount(self, amount: float) -> bool:
        """检查是否有相同金额的pending订单"""
        for order in self.pending_orders.values():
            if abs(order.expected_amount - amount) < 0.001:
                return True
        return False
    
    def match_order(self, amount: float, payer: str) -> Optional[str]:
        """
        根据支付金额和付款人匹配订单
        
        Args:
            amount: 实际支付金额
            payer: 付款人名称
            
        Returns:
            匹配的订单ID，或None
        """
        with self.lock:
            self._cleanup_expired_orders()
            
            # 首先尝试精确匹配金额
            for order_id, order in self.pending_orders.items():
                if abs(order.expected_amount - amount) < 0.01:
                    # 找到匹配
                    order.actual_amount = amount
                    order.payer = payer
                    order.status = "paid"
                    order.paid_at = datetime.now()
                    
                    # 从pending中移除
                    del self.pending_orders[order_id]
                    
                    logger.info(f"Order matched: {order_id} (amount={amount}, payer={payer})")
                    return order_id
            
            # 如果没有精确匹配，尝试模糊匹配（金额相差0.1以内）
            for order_id, order in self.pending_orders.items():
                if abs(order.expected_amount - amount) < 0.1:
                    logger.warning(f"Fuzzy match found: {order_id} (expected={order.expected_amount}, actual={amount})")
                    
                    order.actual_amount = amount
                    order.payer = payer
                    order.status = "paid"
                    order.paid_at = datetime.now()
                    
                    del self.pending_orders[order_id]
                    return order_id
        
        # 未找到匹配
        logger.warning(f"No matching order found for payment: amount={amount}, payer={payer}")
        return None
    
    def create_orphan_payment(self, amount: float, payer: str) -> str:
        """
        创建孤儿支付记录（未匹配到订单的支付）
        
        Returns:
            生成的孤儿支付ID
        """
        orphan_id = f"ORPHAN_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        
        orphan_order = Order(
            order_id=orphan_id,
            base_amount=amount,
            expected_amount=amount,
            actual_amount=amount,
            payer=payer,
            status="orphan",
            paid_at=datetime.now(),
            description="未匹配到订单的支付"
        )
        
        with self.lock:
            self.orders[orphan_id] = orphan_order
        
        logger.warning(f"Orphan payment created: {orphan_id}")
        return orphan_id
    
    def _cleanup_expired_orders(self):
        """清理过期订单"""
        now = datetime.now()
        expired = []
        
        for order_id, order in self.pending_orders.items():
            if now - order.created_at > timedelta(minutes=self.order_expire_minutes):
                expired.append(order_id)
                order.status = "expired"
        
        for order_id in expired:
            del self.pending_orders[order_id]
            logger.info(f"Expired order cleaned up: {order_id}")
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """获取订单信息"""
        return self.orders.get(order_id)
    
    def get_pending_orders(self) -> List[Order]:
        """获取所有待支付订单"""
        with self.lock:
            self._cleanup_expired_orders()
            return list(self.pending_orders.values())
    
    def cancel_order(self, order_id: str) -> bool:
        """取消订单"""
        with self.lock:
            if order_id in self.pending_orders:
                order = self.pending_orders[order_id]
                order.status = "cancelled"
                del self.pending_orders[order_id]
                logger.info(f"Order cancelled: {order_id}")
                return True
        return False
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self.lock:
            self._cleanup_expired_orders()
            
            total = len(self.orders)
            pending = len(self.pending_orders)
            paid = sum(1 for o in self.orders.values() if o.status == "paid")
            expired = sum(1 for o in self.orders.values() if o.status == "expired")
            orphan = sum(1 for o in self.orders.values() if o.status == "orphan")
            
            total_amount = sum(o.actual_amount or 0 for o in self.orders.values() if o.status == "paid")
            
            return {
                "total_orders": total,
                "pending_orders": pending,
                "paid_orders": paid,
                "expired_orders": expired,
                "orphan_payments": orphan,
                "total_revenue": round(total_amount, 2)
            }
    
    def export_orders(self, filepath: str):
        """导出订单数据到JSON文件"""
        with self.lock:
            data = {
                "export_time": datetime.now().isoformat(),
                "orders": {oid: o.to_dict() for oid, o in self.orders.items()}
            }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Orders exported to {filepath}")
    
    def import_orders(self, filepath: str):
        """从JSON文件导入订单数据"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        with self.lock:
            for order_id, order_data in data.get('orders', {}).items():
                order = Order(
                    order_id=order_data['order_id'],
                    base_amount=order_data['base_amount'],
                    expected_amount=order_data['expected_amount'],
                    actual_amount=order_data.get('actual_amount'),
                    status=order_data['status'],
                    payer=order_data.get('payer'),
                    created_at=datetime.fromisoformat(order_data['created_at']) if order_data.get('created_at') else None,
                    paid_at=datetime.fromisoformat(order_data['paid_at']) if order_data.get('paid_at') else None,
                    description=order_data.get('description'),
                    metadata=order_data.get('metadata', {})
                )
                self.orders[order_id] = order
                if order.status == "pending":
                    self.pending_orders[order_id] = order
        
        logger.info(f"Orders imported from {filepath}")


class SimplePaymentMatcher:
    """
    简化版支付匹配器
    用于不需要完整订单系统的场景
    """
    
    def __init__(self):
        self.recent_payments: List[Dict[str, Any]] = []
        self.max_history = 100
    
    def record_payment(self, amount: float, payer: str, order_id: Optional[str] = None):
        """记录支付"""
        payment = {
            "amount": amount,
            "payer": payer,
            "order_id": order_id,
            "timestamp": datetime.now().isoformat()
        }
        
        self.recent_payments.insert(0, payment)
        
        # 限制历史记录数量
        if len(self.recent_payments) > self.max_history:
            self.recent_payments = self.recent_payments[:self.max_history]
        
        logger.info(f"Payment recorded: {amount}元 from {payer}")
    
    def get_recent_payments(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近支付记录"""
        return self.recent_payments[:limit]
    
    def is_duplicate(self, amount: float, payer: str, time_window: int = 60) -> bool:
        """
        检查是否为重复支付
        
        Args:
            amount: 支付金额
            payer: 付款人
            time_window: 检查时间窗口（秒）
            
        Returns:
            是否为重复支付
        """
        now = datetime.now()
        for payment in self.recent_payments:
            try:
                payment_time = datetime.fromisoformat(payment['timestamp'])
                time_diff = (now - payment_time).total_seconds()
                
                if time_diff < time_window:
                    if abs(payment['amount'] - amount) < 0.01 and payment['payer'] == payer:
                        return True
            except:
                continue
        
        return False


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    # 测试订单管理
    config = {
        "enable_auto_order_id": True,
        "order_id_prefix": "WXP",
        "amount_variation_step": 0.01,
        "amount_variation_max": 0.99
    }
    
    manager = OrderManager(config)
    
    # 创建订单
    print("\n=== 创建订单 ===")
    order1 = manager.create_order(30.00, "测试商品1")
    print(f"订单1: {order1.order_id}, 应付金额: {order1.expected_amount}")
    
    order2 = manager.create_order(30.00, "测试商品2")
    print(f"订单2: {order2.order_id}, 应付金额: {order2.expected_amount}")
    
    order3 = manager.create_order(50.00, "测试商品3")
    print(f"订单3: {order3.order_id}, 应付金额: {order3.expected_amount}")
    
    # 匹配支付
    print("\n=== 匹配支付 ===")
    matched = manager.match_order(order1.expected_amount, "用户A")
    print(f"匹配结果: {matched}")
    
    # 获取统计
    print("\n=== 统计 ===")
    stats = manager.get_stats()
    print(json.dumps(stats, indent=2))
