# Linux微信 + VNC远程访问 Docker部署指南

> 一键部署Linux微信客户端，支持VNC远程桌面访问和浏览器访问，同时保持微信支付回调功能。

## 功能特性

- **全容器化部署**：Linux微信、VNC桌面、支付回调服务一体化
- **双模式远程访问**：
  - 原生VNC客户端访问（5901端口）
  - 浏览器直接访问（6080端口，无需安装客户端）
- **微信支付自动回调**：监听微信数据库变化，自动通知业务系统
- **中文环境完整支持**：预装中文字体和输入法
- **持久化存储**：微信登录状态、配置、数据持久保存

## 快速开始

### 1. 环境要求

- Docker 20.10+ 和 Docker Compose 2.0+
- 至少 2GB 可用内存（建议 4GB）
- 开放端口：5901、6080、8888

### 2. 克隆/准备项目

```bash
cd /path/to/your/project
# 确保在项目根目录（包含 docker-compose.vnc.yml 的目录）
```

### 3. 配置环境变量

```bash
# 复制示例配置文件
cp .env.vnc.example .env.vnc

# 编辑配置
nano .env.vnc
```

**关键配置项：**

```bash
# VNC访问密码（必须修改）
VNC_PASSWORD=your_secure_password

# 你的业务系统回调地址（必须修改）
CALLBACK_URL=http://your-server:5000/api/payment/notify/linux_wechat

# 回调签名密钥
CALLBACK_SECRET=your_secret_key_here
```

### 4. 构建并启动

```bash
# 构建镜像（首次需要几分钟）
docker-compose -f docker-compose.vnc.yml build

# 启动服务
docker-compose -f docker-compose.vnc.yml up -d

# 查看日志
docker-compose -f docker-compose.vnc.yml logs -f
```

### 5. 访问微信

**方式一：浏览器访问（推荐）**
- 打开浏览器访问：`http://你的服务器IP:6080/vnc.html`
- 点击 "Connect"
- 输入VNC密码（默认：wechat123，或你设置的 `VNC_PASSWORD`）

**方式二：VNC客户端访问**
- 使用 VNC Viewer、RealVNC、TigerVNC 等客户端
- 连接地址：`你的服务器IP:5901`
- 密码：你设置的VNC密码

### 6. 微信登录

首次启动后：
1. 在VNC桌面中打开微信（桌面有快捷方式或在终端执行 `start-wechat`）
2. 使用手机微信扫码登录
3. 登录成功后，后续重启容器会自动保持登录状态

## 微信支付回调配置

### 业务系统回调URL

容器启动后会监听 `http://0.0.0.0:8888`，你的业务系统需要配置：

```bash
# 回调地址格式
http://<docker主机IP>:8888/api/payment/notify/linux_wechat
```

### 回调数据格式

```json
{
  "order_id": "WXP20240101120000123456",
  "amount": 30.01,
  "payer": "微信用户昵称",
  "timestamp": "2024-01-01T12:00:01",
  "payment_method": "wechat",
  "source": "biz_message_db",
  "signature": "sha256=abc123..."
}
```

### 签名验证

```python
import hmac
import hashlib

def verify_signature(payload: dict, secret_key: str, signature: str) -> bool:
    # 拼接签名字符串（按key排序，排除signature字段）
    sign_str = "&".join(f"{k}={v}" for k, v in sorted(payload.items()) if k != 'signature')
    
    expected = hmac.new(
        secret_key.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return signature == expected
```

## 常用命令

```bash
# 查看服务状态
docker-compose -f docker-compose.vnc.yml ps

# 查看实时日志
docker-compose -f docker-compose.vnc.yml logs -f wechat-vnc

# 进入容器内部
docker-compose -f docker-compose.vnc.yml exec wechat-vnc bash

# 重启服务
docker-compose -f docker-compose.vnc.yml restart

# 停止服务
docker-compose -f docker-compose.vnc.yml down

# 完全清理（包括数据卷）
docker-compose -f docker-compose.vnc.yml down -v

# 重新构建镜像
docker-compose -f docker-compose.vnc.yml build --no-cache
```

## 目录结构说明

```
linux_wechat_pay/
├── docker/
│   ├── Dockerfile.vnc          # VNC版Docker镜像构建文件
│   ├── xstartup                # VNC X会话启动脚本
│   ├── start-vnc.sh            # 容器启动脚本
│   └── supervisord.conf        # 进程管理配置
├── docker-compose.vnc.yml      # Docker Compose编排文件
├── .env.vnc.example            # 环境变量示例
├── config/
│   └── vnc/
│       └── config.yaml         # VNC环境专用配置文件
└── docs/
    └── vnc-deployment.md       # 本文档
```

## 端口说明

| 端口 | 用途 | 访问方式 |
|------|------|----------|
| 5901 | VNC原生访问 | VNC客户端（如RealVNC） |
| 6080 | noVNC浏览器访问 | 浏览器访问 http://IP:6080/vnc.html |
| 8888 | 微信支付回调API | HTTP API |
| 8889 | 管理API | HTTP API |

## 安全建议

1. **修改默认VNC密码**：务必在 `.env.vnc` 中设置强密码
2. **限制端口访问**：使用防火墙限制5901、6080端口的访问IP
3. **启用HTTPS**：生产环境建议通过Nginx反向代理启用HTTPS
4. **回调签名验证**：业务系统必须验证回调签名
5. **定期备份数据**：备份 `./data` 目录防止数据丢失

## 故障排查

### 无法访问VNC

```bash
# 检查容器状态
docker-compose -f docker-compose.vnc.yml ps

# 查看VNC相关日志
docker-compose -f docker-compose.vnc.yml logs wechat-vnc | grep -i vnc

# 检查端口监听
docker-compose -f docker-compose.vnc.yml exec wechat-vnc netstat -tlnp
```

### 微信无法启动

```bash
# 进入容器手动启动微信查看错误
docker-compose -f docker-compose.vnc.yml exec wechat-vnc bash
start-wechat

# 检查微信进程
ps aux | grep wechat

# 查看日志
cat /var/log/supervisor/wechat-client.log
```

### 支付回调不生效

```bash
# 检查回调服务日志
docker-compose -f docker-compose.vnc.yml logs wechat-vnc | grep callback

# 测试API连通性
curl http://localhost:8888/health
```

更多故障排查请参考 `docs/troubleshooting.md`

## 性能优化

### 调整分辨率

编辑 `.env.vnc`：
```bash
VNC_RESOLUTION=1920x1080  # 更高分辨率
VNC_RESOLUTION=1024x768   # 更低分辨率，更流畅
```

### 限制资源使用

编辑 `docker-compose.vnc.yml` 中的 `deploy.resources` 部分。

### 使用GPU加速（可选）

如果主机有GPU，可以修改 `devices` 配置启用硬件加速。

## 更新升级

```bash
# 拉取最新代码
git pull

# 重新构建镜像
docker-compose -f docker-compose.vnc.yml build

# 重启服务
docker-compose -f docker-compose.vnc.yml up -d
```

## 许可证

MIT License - 详见项目根目录 LICENSE 文件

## 支持与反馈

- 提交Issue
- 邮件联系
