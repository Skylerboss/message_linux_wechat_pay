#!/bin/bash

# Linux WeChat Pay Callback Service - 交互式配置脚本
# 用于生成配置文件和 docker-compose.yml

set -e

echo "==================================================================="
echo "  Linux WeChat Pay Callback Service - 配置向导"
echo "==================================================================="
echo ""

# 检查是否已有配置文件
if [ -f "config.env" ]; then
    echo "发现已存在的配置文件 config.env"
    read -p "是否重新配置? (y/N): " RECONFIG
    if [[ ! "$RECONFIG" =~ ^[Yy]$ ]]; then
        echo "使用现有配置文件。"
        exit 0
    fi
    rm config.env
fi

# 回调配置
echo "请输入回调配置:"
echo "提示: 回调URL用于接收微信支付通知"
read -p "回调IP地址 (默认: host.docker.internal): " CALLBACK_IP
CALLBACK_IP=${CALLBACK_IP:-host.docker.internal}
read -p "回调端口 (默认: 5000): " CALLBACK_PORT
CALLBACK_PORT=${CALLBACK_PORT:-5000}
read -p "回调密钥 (默认: test_secret): " CALLBACK_SECRET
CALLBACK_SECRET=${CALLBACK_SECRET:-test_secret}

CALLBACK_URL="http://${CALLBACK_IP}:${CALLBACK_PORT}/api/payment/notify/linux_wechat"

# VNC 配置
echo ""
echo "请输入 VNC 配置:"
read -p "VNC 密码 (默认: wechat123): " VNC_PASSWORD
VNC_PASSWORD=${VNC_PASSWORD:-wechat123}
read -p "VNC 分辨率 (默认: 1280x800): " VNC_RESOLUTION
VNC_RESOLUTION=${VNC_RESOLUTION:-1280x800}

# 时区配置
echo ""
read -p "时区 (默认: Asia/Shanghai): " TIMEZONE
TIMEZONE=${TIMEZONE:-Asia/Shanghai}

# 生成配置文件
cat > config.env <<EOF
# Linux WeChat Pay Callback Service - 配置文件
# 由 setup.sh 自动生成

# 回调配置
CALLBACK_URL=${CALLBACK_URL}
CALLBACK_SECRET_KEY=${CALLBACK_SECRET}

# VNC 配置
VNC_PW=${VNC_PASSWORD}
VNC_RESOLUTION=${VNC_RESOLUTION}

# 时区设置
TZ=${TIMEZONE}
EOF

echo ""
echo "==================================================================="
echo "配置文件已生成: config.env"
echo "==================================================================="
echo ""
echo "配置内容:"
cat config.env
echo ""
echo "==================================================================="
echo "下一步:"
echo "1. 检查配置文件 config.env"
echo "2. 运行: docker-compose up -d"
echo "3. 访问: http://localhost:6080/vnc.html"
echo "==================================================================="
