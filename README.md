# Linux 微信支付回调系统

[![Docker Image](https://img.shields.io/badge/Docker-阿里云镜像-blue)](https://cr.console.aliyun.com/)
[![Version](https://img.shields.io/badge/Version-1.0.0-green)()

> Linux 微信支付回调系统 Docker 容器化部署方案，支持自动检测微信支付并回调通知

## 特性

- ✅ 一键安装/更新服务
- ✅ 自动检测微信支付（二维码赞赏到账）
- ✅ 支持签名验证，确保回调安全
- ✅ 跨源去重，避免重复回调
- ✅ 支持环境变量配置
- ✅ 与 Message Bot 无缝集成

## 快速开始

### 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/Skylerboss/message_linux_wechat_pay/main/install.sh | bash
```

### 手动下载安装

```bash
wget https://raw.githubusercontent.com/Skylerboss/message_linux_wechat_pay/main/install.sh
bash install.sh
```

## 环境要求

- Linux 服务器（推荐 Ubuntu 20.04+）
- Docker 20.10+
- Docker Compose 1.29+
- Message Bot 服务（用于接收回调）

## 配置说明

首次安装时会创建 `.env` 配置文件：

```env
# 回调地址（设置为 Message Bot 的地址）
CALLBACK_URL=http://192.168.100.7:5000/api/payment/notify/linux_wechat

# 回调密钥（可选，用于签名验证）
CALLBACK_SECRET_KEY=your_secret_key

# 解密项目目录
DECRYPT_PROJECT_DIR=/root/wechat-decrypt
```

### 配置项说明

| 环境变量 | 说明 | 默认值 |
|---------|------|-------|
| CALLBACK_URL | 回调通知地址 | 无（必填） |
| CALLBACK_SECRET_KEY | 回调签名密钥 | 空 |
| DECRYPT_PROJECT_DIR | 微信解密项目目录 | /root/wechat-decrypt |

## 使用方法

### 交互式菜单（推荐）

直接运行脚本进入交互菜单：

```bash
bash install.sh
```

菜单选项：
| 选项 | 功能 |
|-----|------|
| 1 | 安装/更新服务 |
| 2 | 修改配置 |
| 3 | 查看服务状态 |
| 4 | 查看日志 |
| 5 | 重启服务 |
| 6 | 停止服务 |
| 7 | 卸载服务 |
| 0 | 退出 |

### 命令行参数

```bash
# 交互式安装（首次）
bash install.sh

# 查看日志
docker logs -f linux-wechat-pay

# 停止服务
cd /root/linux_wechat_pay && docker-compose down

# 重启服务
cd /root/linux_wechat_pay && docker-compose restart
```

## Message Bot 配置

在 Message Bot 中配置 Linux 微信支付：

1. 确保 Message Bot 运行正常
2. 在支付配置中添加 `linux_wechat` 配置：
   - 回调地址：`/api/payment/notify/linux_wechat`
   - 密钥：与 CALLBACK_SECRET_KEY 一致

## 常见问题

### 1. 镜像拉取失败

```bash
# 检查网络
curl -I https://registry.cn-hangzhou.aliyuncs.com

# 手动拉取
docker pull registry.cn-hangzhou.aliyuncs.com/skylerboss/message_linux_wechat_pay:latest
```

### 2. 回调未收到

```bash
# 检查服务日志
docker logs linux-wechat-pay

# 检查回调发送记录
docker exec linux-wechat-vnc grep -E 'DETECTED|Callback sent' /tmp/wechat-pay.log
```

### 3. 签名验证失败

确保 Linux 端和 Message Bot 端使用相同的签名算法和密钥。

## 技术细节

### 系统架构

```
┌─────────────────────────────────────────────────────┐
│              Linux WeChat Docker 容器               │
├─────────────────────────────────────────────────────┤
│  session_db (PayMsg表) → db_monitor → 去重 → 回调  │
│         ↓                                           │
│  callback_sender → HTTP POST 到 Message Bot        │
└─────────────────────────────────────────────────────┘
```

### 支付检测流程

```
用户支付 → 微信数据库变更 → 检测到支付 → 跨源去重 → 发送回调
                                            ↓
                                    Message Bot 端去重
```

### 优化参数

| 参数 | 默认值 | 说明 |
|-----|-------|------|
| poll_interval | 1.0s | 数据库轮询间隔 |
| 延迟 | ~23秒 | 支付到回调的总时间 |

## 更新日志

### v1.0.0 (2026-04-29)
- 初始版本
- 支持二维码赞赏到账检测
- 支持签名验证
- 支持跨源去重
- 支持环境变量配置

## 相关项目

- [Message Bot](https://github.com/Skylerboss/MessageBot) - 消息机器人框架
- [PMHQ](https://github.com/Skylerboss/message_qq) - QQ 消息网关

## 许可证

MIT License