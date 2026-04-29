# Linux WeChat Pay Callback Service - VNC 版本

## 快速开始

### 1. 配置环境

首次使用需要运行配置脚本：

**Windows:**
```cmd
setup.bat
```

**Linux/Mac:**
```bash
bash setup.sh
```

配置脚本会提示你输入：
- 回调 IP 地址（默认: host.docker.internal）
- 回调端口（默认: 5000）
- 回调密钥（默认: test_secret）
- VNC 密码（默认: wechat123）
- VNC 分辨率（默认: 1280x800）
- 时区（默认: Asia/Shanghai）

配置完成后会生成 `config.env` 文件。

### 2. 启动服务

```bash
docker-compose -f docker-compose.vnc.yml up -d
```

### 3. 访问服务

- **VNC 浏览器访问**: http://localhost:6080/vnc.html
- **VNC 原生访问**: vnc://localhost:5901
- **支付回调 API**: http://localhost:8888
- **健康检查**: http://localhost:8888/health

### 4. 登录微信

通过 VNC 访问微信，扫码登录。

登录后，系统会自动：
1. 检测微信数据库路径（支持不同用户ID）
2. 提取数据库密钥
3. 解密数据库
4. 启动支付回调监听

## 配置文件

### config.env

配置文件示例：

```bash
# 回调配置
CALLBACK_URL=http://host.docker.internal:5000/api/payment/notify/linux_wechat
CALLBACK_SECRET_KEY=test_secret

# VNC 配置
VNC_PW=wechat123
VNC_RESOLUTION=1280x800

# 时区设置
TZ=Asia/Shanghai
```

### 修改配置

如需修改配置，可以：

1. 重新运行 `bash setup.sh`
2. 或直接编辑 `config.env` 文件
3. 然后重启容器：`docker-compose -f docker-compose.vnc.yml restart`

## 数据持久化

以下目录会持久化到宿主机：

- `./data` - 应用数据
- `./data/wechat` - 微信配置
- `./data/fcitx` - 输入法配置
- `./logs` - 日志文件
- `./data/vnc` - VNC 配置

## 常见问题

### 微信数据库路径检测失败

系统会自动检测 `/root/Documents/xwechat_files/*/db_storage` 路径，支持不同用户ID。

如果检测失败，可以手动检查：

```bash
docker exec -it linux-wechat-vnc find /root/Documents -type d -name "db_storage"
```

### 回调未触发

1. 检查回调 URL 是否正确
2. 检查回调服务是否运行
3. 查看日志：`docker logs -f linux-wechat-vnc`

### VNC 连接失败

1. 检查端口是否被占用
2. 检查防火墙设置
3. 使用浏览器访问 http://localhost:6080/vnc.html

## 环境变量说明

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| CALLBACK_URL | 回调通知 URL | http://host.docker.internal:5000/api/payment/notify/linux_wechat |
| CALLBACK_SECRET_KEY | 回调签名密钥 | test_secret |
| VNC_PW | VNC 密码 | wechat123 |
| VNC_RESOLUTION | VNC 分辨率 | 1280x800 |
| TZ | 时区 | Asia/Shanghai |
