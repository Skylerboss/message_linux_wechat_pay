# Docker VNC 部署文件

本目录包含 Linux微信 + VNC远程访问 的Docker部署相关文件。

## 文件说明

| 文件 | 用途 |
|------|------|
| `Dockerfile.vnc` | VNC版Docker镜像构建文件 |
| `xstartup` | VNC X会话启动脚本（启动XFCE桌面和输入法） |
| `start-vnc.sh` | 容器入口脚本（初始化VNC和noVNC） |
| `supervisord.conf` | 进程管理配置（管理DBus、VNC、支付服务） |
| `README.md` | 本文件 |

## 快速开始

详细部署说明请参考：[docs/vnc-deployment.md](../docs/vnc-deployment.md)

### 一键启动

```bash
# 1. 复制环境变量示例并编辑
cp .env.vnc.example .env.vnc
nano .env.vnc

# 2. 构建并启动
docker-compose -f docker-compose.vnc.yml up -d

# 3. 浏览器访问 http://你的IP:6080/vnc.html
```

## 架构说明

详细架构文档请参考：[docs/architecture.md](../docs/architecture.md)

```
┌─────────────────────────────────────────────┐
│              Docker 容器                   │
│  ┌─────────────────────────────────────────┐│
│  │       Supervisor 进程管理器              ││
│  │  ┌────────┐ ┌────────┐ ┌──────────────┐││
│  │  │  DBus  │ │TigerVNC│ │ 支付回调服务  │││
│  │  │  (:0) │ │(:1/5901)│ │  (:8888)    │││
│  │  └───┬────┘ └────┬───┘ └──────┬───────┘││
│  │      └───────────┼────────────┘        ││
│  │                  ▼                     ││
│  │         ┌──────────────────┐          ││
│  │         │   XFCE 桌面 (:1)  │          ││
│  │         │  ┌────────────┐   │          ││
│  │         │  │  微信     │   │          ││
│  │         │  │  客户端   │   │          ││
│  │         │  └────────────┘   │          ││
│  │         └──────────────────┘          ││
│  └─────────────────────────────────────────┘│
│                     │                       │
│              noVNC (:6080)                  │
└─────────────────────┼───────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
  ┌──────────────┐          ┌──────────────┐
  │  VNC 客户端   │          │   浏览器      │
  │  (5901端口)   │          │  (6080端口)   │
  └──────────────┘          └──────────────┘
```

## 端口映射

| 容器端口 | 外部端口 | 用途 |
|----------|----------|------|
| 5901 | 5901 | VNC原生访问 |
| 6080 | 6080 | noVNC浏览器访问 |
| 8888 | 8888 | 支付回调API |
| 8889 | 8889 | 管理API |

## 故障排查

遇到问题请参考：[docs/troubleshooting.md](../docs/troubleshooting.md)

## 自定义构建

如需自定义镜像（如更换微信版本、添加额外软件）：

```bash
# 编辑 Dockerfile.vnc
nano docker/Dockerfile.vnc

# 重新构建
docker-compose -f docker-compose.vnc.yml build --no-cache

# 重启
docker-compose -f docker-compose.vnc.yml up -d
```

## 安全提示

- 生产环境务必修改默认VNC密码
- 建议通过HTTPS反向代理访问
- 限制VNC端口仅允许可信IP访问
