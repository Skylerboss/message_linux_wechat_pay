#!/usr/bin/env python3
"""
微信Linux版进程检测和自动启动工具
"""
import subprocess
import time
import logging
import psutil

logger = logging.getLogger(__name__)


class WeChatMonitor:
    """微信进程监控器"""
    
    def __init__(self, auto_start: bool = False, wechat_path: str = None):
        self.auto_start = auto_start
        self.wechat_path = wechat_path or self._find_wechat_path()
    
    def _find_wechat_path(self) -> str:
        """查找微信可执行文件路径"""
        # 常见路径
        common_paths = [
            "/usr/bin/wechat",
            "/usr/bin/weixin",
            "/opt/wechat/wechat",
            "/opt/weixin/weixin",
            "/snap/bin/wechat",
            "/usr/local/bin/wechat",
            # AppImage
            "~/WeChatLinux_x86_64.AppImage",
            "~/Downloads/WeChatLinux_x86_64.AppImage",
        ]
        
        import os
        for path in common_paths:
            expanded = os.path.expanduser(path)
            if os.path.exists(expanded):
                return expanded
        
        return None
    
    def is_wechat_running(self) -> bool:
        """检查微信是否在运行"""
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                name = proc.info['name'].lower()
                if 'wechat' in name or 'weixin' in name:
                    return True
                
                # 检查命令行参数
                cmdline = ' '.join(proc.info['cmdline'] or []).lower()
                if 'wechat' in cmdline or 'weixin' in cmdline:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        return False
    
    def start_wechat(self) -> bool:
        """启动微信"""
        if not self.wechat_path:
            logger.error("微信路径未找到")
            return False
        
        try:
            # 后台启动
            subprocess.Popen(
                [self.wechat_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            logger.info(f"微信已启动: {self.wechat_path}")
            time.sleep(3)  # 等待启动
            return True
        except Exception as e:
            logger.error(f"启动微信失败: {e}")
            return False
    
    def ensure_wechat_running(self) -> bool:
        """确保微信在运行"""
        if self.is_wechat_running():
            logger.info("微信已在运行")
            return True
        
        if self.auto_start:
            logger.info("微信未运行，尝试自动启动...")
            return self.start_wechat()
        else:
            logger.warning("微信未运行，请手动启动")
            return False
    
    def monitor_loop(self, interval: int = 30):
        """持续监控微信进程"""
        logger.info("开始监控微信进程...")
        
        while True:
            if not self.is_wechat_running():
                logger.warning("微信进程已退出")
                
                if self.auto_start:
                    logger.info("尝试重新启动微信...")
                    self.start_wechat()
            
            time.sleep(interval)


def main():
    """测试函数"""
    logging.basicConfig(level=logging.INFO)
    
    monitor = WeChatMonitor(auto_start=False)
    
    print("检查微信运行状态...")
    if monitor.is_wechat_running():
        print("✓ 微信正在运行")
    else:
        print("✗ 微信未运行")
        print(f"微信路径: {monitor.wechat_path or '未找到'}")


if __name__ == "__main__":
    main()
