# Linux微信 + VNC Docker 故障排查手册

## 快速诊断清单

遇到问题时，按以下顺序排查：

1. [ ] 容器是否运行正常？
2. [ ] 端口是否正确映射？
3. [ ] 服务日志是否有错误？
4. [ ] 配置文件是否正确？

## 常见问题

### 1. 无法连接VNC

#### 症状
- VNC客户端连接超时
- 浏览器访问 `http://IP:6080` 无响应

#### 排查步骤

```bash
# 1. 检查容器状态
docker-compose -f docker-compose.vnc.yml ps

# 应该显示类似：
# NAME              STATUS          PORTS
# linux-wechat-vnc  Up 5 minutes    0.0.0.0:5901->5901/tcp, 0.0.0.0:6080->6080/tcp, 0.0.0.0:8888->8888/tcp

# 2. 检查端口监听
docker-compose -f docker-compose.vnc.yml exec wechat-vnc netstat -tlnp

# 应该显示：
# tcp  0  0 0.0.0.0:5901  0.0.0.0:*  LISTEN  -  (TigerVNC)
# tcp  0  0 0.0.0.0:6080  0.0.0.0:*  LISTEN  -  (websockify)

# 3. 查看VNC相关日志
docker-compose -f docker-compose.vnc.yml logs wechat-vnc | grep -i vnc

# 4. 检查防火墙
docker-compose -f docker-compose.vnc.yml exec wechat-vnc iptables -L
```

#### 常见原因和解决

| 原因 | 症状 | 解决 |
|------|------|------|
| 端口未暴露 | 端口未在 `docker ps` 中显示 | 检查 `docker-compose.vnc.yml` 的 `ports` 配置 |
| 防火墙阻挡 | 外部无法访问，容器内正常 | 开放宿主机防火墙的 5901、6080 端口 |
| VNC未启动 | 日志显示 "VNC server failed" | 检查 `start-vnc.sh` 日志，重置VNC |
| 密码错误 | 连接后立即断开 | 确认 `.env.vnc` 中 `VNC_PASSWORD` 设置正确 |

#### 重置VNC

```bash
# 进入容器
docker-compose -f docker-compose.vnc.yml exec wechat-vnc bash

# 停止VNC
vncserver -kill :1

# 清理锁文件
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1

# 重新设置密码
echo "newpassword" | vncpasswd -f > /root/.vnc/passwd
chmod 600 /root/.vnc/passwd

# 重启容器
docker-compose -f docker-compose.vnc.yml restart
```

### 2. 微信无法启动

#### 症状
- 点击微信图标无反应
- 终端执行 `start-wechat` 报错
- 微信窗口闪退

#### 排查步骤

```bash
# 1. 进入容器
docker-compose -f docker-compose.vnc.yml exec wechat-vnc bash

# 2. 检查微信文件
ls -la /opt/wechat/

# 3. 手动启动查看错误
export DISPLAY=:1
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
/opt/wechat/wechat 2>&1

# 4. 检查微信日志
ls -la /root/.config/wechat/
cat /root/.config/wechat/log/

# 5. 检查依赖库
ldd /opt/wechat/wechat | grep "not found"

# 6. 查看supervisor日志
tail -f /var/log/supervisor/wechat-client-error.log
```

#### 常见原因和解决

| 原因 | 症状 | 解决 |
|------|------|------|
| DISPLAY未设置 | 报错 "cannot open display" | 确认 `DISPLAY=:1` |
| 缺少依赖库 | ldd显示"not found" | 安装缺失库：`apt-get install libxxx` |
| 配置文件损坏 | 启动后闪退 | 删除配置：`rm -rf /root/.config/wechat` |
| UOS兼容层问题 | 提示授权错误 | 检查 `/usr/lib/libuosdevicea.so` 是否存在 |
| 权限问题 | Permission denied | 检查微信目录权限 |

#### 重装微信

```bash
docker-compose -f docker-compose.vnc.yml exec wechat-vnc bash

# 备份配置
cp -r /root/.config/wechat /tmp/wechat-backup

# 卸载/重装
apt-get remove wechat
rm -rf /opt/wechat

# 重新下载安装
wget https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb -O /tmp/wechat.deb
dpkg -i /tmp/wechat.deb || apt-get install -f -y

# 恢复配置
cp -r /tmp/wechat-backup/* /root/.config/wechat/
```

### 3. 支付回调不生效

#### 症状
- 用户支付后无回调通知
- 日志显示 "Callback failed"
- 业务系统未收到请求

#### 排查步骤

```bash
# 1. 检查回调服务是否运行
curl http://localhost:8888/health

# 应该返回：
# {"status":"healthy","timestamp":"...","version":"..."}

# 2. 查看回调日志
docker-compose -f docker-compose.vnc.yml logs wechat-vnc | grep -i callback

# 3. 检查数据库监听是否正常
docker-compose -f docker-compose.vnc.yml logs wechat-vnc | grep -i "database\|monitor"

# 4. 测试回调URL
docker-compose -f docker-compose.vnc.yml exec wechat-vnc bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"test": true}' \
  http://host.docker.internal:5000/api/payment/notify/linux_wechat

# 5. 检查配置文件
cat /app/config.yaml | grep -A 5 callback
```

#### 常见原因和解决

| 原因 | 症状 | 解决 |
|------|------|------|
| 回调URL错误 | 日志显示 "Connection refused" | 检查 `.env.vnc` 中 `CALLBACK_URL`，确保使用 `host.docker.internal` 或正确的IP |
| 业务系统未启动 | "Connection refused" 或超时 | 确保业务系统监听端口并允许容器访问 |
| 防火墙阻挡 | 连接超时 | 开放业务系统的回调端口 |
| 数据库未解密 | 无 "Detected encrypted DB change" 日志 | 检查微信数据库解密配置 |
| 签名验证失败 | 业务系统返回 401 | 检查 `CALLBACK_SECRET` 是否匹配 |

#### 手动测试支付检测

```bash
# 进入容器
docker-compose -f docker-compose.vnc.yml exec wechat-vnc bash

# 检查数据库文件是否存在
ls -la /root/wechat-decrypt/decrypted/

# 手动触发解密
python3 /app/decrypt_db.py --force

# 查看解密结果
ls -la /root/wechat-decrypt/decrypted/
```

### 4. 中文显示乱码

#### 症状
- 微信界面中文显示为方块或乱码
- 输入法候选框无文字

#### 排查步骤

```bash
# 1. 检查字体
docker-compose -f docker-compose.vnc.yml exec wechat-vnc fc-list :lang=zh

# 应该显示中文字体列表

# 2. 检查locale
docker-compose -f docker-compose.vnc.yml exec wechat-vnc locale

# 应该显示：
# LANG=zh_CN.UTF-8
# LC_ALL=zh_CN.UTF-8

# 3. 检查输入法
docker-compose -f docker-compose.vnc.yml exec wechat-vnc fcitx-diagnose
```

#### 解决

```bash
docker-compose -f docker-compose.vnc.yml exec wechat-vnc bash

# 重新生成locale
locale-gen zh_CN.UTF-8

# 更新字体缓存
fc-cache -fv

# 重启fcitx
pkill fcitx || true
fcitx-autostart &
```

### 5. 容器启动失败

#### 症状
- `docker-compose up` 后立即退出
- 日志显示错误后容器停止

#### 排查步骤

```bash
# 1. 查看完整日志
docker-compose -f docker-compose.vnc.yml logs

# 2. 前台运行查看详细错误
docker-compose -f docker-compose.vnc.yml up  # 不加 -d

# 3. 检查卷挂载
docker-compose -f docker-compose.vnc.yml config

# 4. 检查配置文件格式
docker-compose -f docker-compose.vnc.yml config -q
```

#### 常见原因和解决

| 原因 | 症状 | 解决 |
|------|------|------|
| 配置文件语法错误 | "yaml: line xx" | 检查 `.env.vnc` 和 `docker-compose.vnc.yml` 格式 |
| 卷挂载失败 | "bind mount failed" | 确保主机路径存在且有权限 |
| 端口冲突 | "port is already allocated" | 修改 `.env.vnc` 中的端口映射 |
| 镜像构建失败 | "build failed" | 检查 Dockerfile 和网络连接 |
| 内存不足 | OOM killed | 增加Docker内存限制或宿主机内存 |

### 6. VNC连接但显示黑屏

#### 症状
- VNC连接成功，输入密码后黑屏
- 无桌面环境显示

#### 排查步骤

```bash
# 1. 检查X会话
docker-compose -f docker-compose.vnc.yml exec wechat-vnc ps aux | grep -i "Xvnc\|xfce"

# 2. 检查xstartup权限
docker-compose -f docker-compose.vnc.yml exec wechat-vnc ls -la /root/.vnc/xstartup

# 3. 查看VNC日志
docker-compose -f docker-compose.vnc.yml exec wechat-vnc cat /root/.vnc/*.log

# 4. 检查DISPLAY
docker-compose -f docker-compose.vnc.yml exec wechat-vnc echo $DISPLAY
# 应该输出 :1
```

#### 解决

```bash
docker-compose -f docker-compose.vnc.yml exec wechat-vnc bash

# 修复xstartup权限
chmod +x /root/.vnc/xstartup

# 重启VNC
vncserver -kill :1
rm -f /tmp/.X1-lock
vncserver :1 -depth 24 -geometry 1280x800

# 如果桌面未启动，手动启动
export DISPLAY=:1
startxfce4 &
```

### 7. 微信支付重复回调

#### 症状
- 同一笔支付多次触发回调
- 业务系统收到重复通知

#### 排查步骤

```bash
# 查看日志中的去重记录
docker-compose -f docker-compose.vnc.yml logs wechat-vnc | grep -i "duplicate\|dedup"

# 查看回调历史
docker-compose -f docker-compose.vnc.yml logs wechat-vnc | grep "Callback sent"
```

#### 解决

检查 `config.yaml` 中的去重配置：

```yaml
deduplication:
  time_window: 30  # 增加去重窗口（秒）
  history_size: 1000
```

同时在业务系统端实现幂等处理，通过 `order_id` 去重。

### 8. 性能问题

#### 症状
- VNC操作卡顿
- 微信响应慢
- 容器CPU/内存占用高

#### 排查步骤

```bash
# 1. 查看资源使用
docker stats linux-wechat-vnc

# 2. 查看进程资源占用
docker-compose -f docker-compose.vnc.yml exec wechat-vnc ps aux --sort=-%mem

# 3. 检查VNC带宽
docker-compose -f docker-compose.vnc.yml exec wechat-vnc cat /proc/net/dev
```

#### 优化建议

```bash
# 1. 降低分辨率（编辑 .env.vnc）
VNC_RESOLUTION=1024x768

# 2. 限制微信资源（在VNC客户端设置低质量模式）

# 3. 重启释放内存
docker-compose -f docker-compose.vnc.yml restart

# 4. 增加容器资源限制（编辑 docker-compose.vnc.yml）
deploy:
  resources:
    limits:
      cpus: '2.0'  # 限制CPU
      memory: 2G   # 限制内存
```

## 日志收集

### 收集完整诊断信息

```bash
#!/bin/bash
# save_diagnosis.sh

OUTPUT="diagnosis_$(date +%Y%m%d_%H%M%S).txt"

echo "========== 容器状态 ==========" >> $OUTPUT
docker-compose -f docker-compose.vnc.yml ps >> $OUTPUT 2>&1

echo "" >> $OUTPUT
echo "========== 最近100行日志 ==========" >> $OUTPUT
docker-compose -f docker-compose.vnc.yml logs --tail=100 >> $OUTPUT 2>&1

echo "" >> $OUTPUT
echo "========== 进程列表 ==========" >> $OUTPUT
docker-compose -f docker-compose.vnc.yml exec wechat-vnc ps aux >> $OUTPUT 2>&1

echo "" >> $OUTPUT
echo "========== 端口监听 ==========" >> $OUTPUT
docker-compose -f docker-compose.vnc.yml exec wechat-vnc netstat -tlnp >> $OUTPUT 2>&1

echo "" >> $OUTPUT
echo "========== 磁盘使用 ==========" >> $OUTPUT
docker-compose -f docker-compose.vnc.yml exec wechat-vnc df -h >> $OUTPUT 2>&1

echo "" >> $OUTPUT
echo "========== 配置文件 ==========" >> $OUTPUT
cat .env.vnc >> $OUTPUT 2>&1
cat config.yaml >> $OUTPUT 2>&1

echo "诊断信息已保存到: $OUTPUT"
```

## 联系支持

如果以上方法无法解决问题，请提供以下信息：

1. 运行 `save_diagnosis.sh` 收集的诊断信息
2. 宿主机系统版本 (`uname -a`)
3. Docker版本 (`docker version`)
4. 问题复现步骤
