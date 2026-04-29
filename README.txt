============================================
Linux 微信支付回调系统 - 访问说明
============================================

【VNC 远程桌面】
- 地址: vnc://192.168.100.24:5901
- 密码: wechat123

【Web 浏览器访问】
- 地址: http://192.168.100.24:6080/vnc.html
- 点击 "Connect" 按钮连接

【API 接口】
- 地址: http://192.168.100.24:8888
- 健康检查: http://192.168.100.24:8888/health

【日志查看】
- docker logs -f linux-wechat-pay

【服务管理】
- 启动: cd /root/linux_wechat_pay && docker-compose up -d
- 停止: cd /root/linux_wechat_pay && docker-compose down
- 重启: cd /root/linux_wechat_pay && docker-compose restart

【配置修改】
- 编辑: vim /root/linux_wechat_pay/.env

============================================