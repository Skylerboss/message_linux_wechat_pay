#!/bin/bash
# -*- coding: utf-8 -*-
# Linux 微信支付回调系统一键安装脚本

set -e

# 配置
NAMESPACE="${WECHAT_NAMESPACE:-skylerboss}"
REPO_NAME="${WECHAT_REPO:-message_linux_wechat_pay}"
REGISTRY="${WECHAT_REGISTRY:-registry.cn-hangzhou.aliyuncs.com}"
INSTALL_DIR="${WECHAT_INSTALL_DIR:-/root/linux_wechat_pay}"
MESSAGE_BOT_URL="${MESSAGE_BOT_URL:-http://192.168.100.7:5000}"
CALLBACK_SECRET="${CALLBACK_SECRET:-}"

VERSION="1.0.0"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   Linux 微信支付回调系统 v${VERSION}        ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════╝${NC}"
echo ""

# 检测 docker compose
COMPOSE_CMD="docker-compose"
if ! command -v docker-compose &> /dev/null; then
    if docker compose version &> /dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
    fi
fi

# 拉取镜像
full_image="${REGISTRY}/${NAMESPACE}/${REPO_NAME}:latest"
echo -e "${BLUE}📥 拉取镜像: ${full_image}${NC}"
docker pull "${full_image}"
echo -e "${GREEN}✅ 镜像拉取成功${NC}"

# 创建目录
echo -e "${BLUE}📁 设置安装目录: ${INSTALL_DIR}${NC}"
mkdir -p "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/wechat-decrypt"
echo -e "${GREEN}✅ 目录创建完成${NC}"

# 创建 .env
echo -e "${BLUE}📝 创建 .env 配置文件${NC}"
cat > "${INSTALL_DIR}/.env" << ENVEOF
CALLBACK_URL=${MESSAGE_BOT_URL}/api/payment/notify/linux_wechat
CALLBACK_SECRET_KEY=${CALLBACK_SECRET}
DECRYPT_PROJECT_DIR=/root/wechat-decrypt
ENVEOF
echo -e "${GREEN}✅ 配置文件创建完成${NC}"

# 创建 docker-compose.yml
echo -e "${BLUE}📝 创建 docker-compose.yml${NC}"
cat > "${INSTALL_DIR}/docker-compose.yml" << COMPOSEEOF
version: '3.8'

services:
  linux-wechat-pay:
    image: ${full_image}
    container_name: linux-wechat-pay
    privileged: true
    restart: unless-stopped
    ports:
      - "5901:5901"
      - "6080:6080"
      - "8888:8888"
    volumes:
      - ./wechat-decrypt:/root/wechat-decrypt
      - ./data:/root/.config/QQ
    env_file:
      - .env
    environment:
      - CALLBACK_URL=${MESSAGE_BOT_URL}/api/payment/notify/linux_wechat
      - CALLBACK_SECRET_KEY=${CALLBACK_SECRET}

networks:
  default:
    driver: bridge
COMPOSEEOF
echo -e "${GREEN}✅ docker-compose.yml 创建完成${NC}"

# 启动服务
echo -e "${BLUE}🚀 启动服务...${NC}"
cd "${INSTALL_DIR}"
${COMPOSE_CMD} up -d
echo -e "${GREEN}✅ 服务启动成功${NC}"

# 保存版本
echo "${VERSION}" > "${INSTALL_DIR}/.version"

# 显示状态
echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Linux 微信支付回调系统 安装完成${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""
echo -e "📦 镜像: ${full_image}"
echo -e "📁 安装目录: ${INSTALL_DIR}"
echo ""
echo -e "${GREEN}VNC 访问:${NC} vnc://localhost:5901 (密码: wechat123)"
echo -e "${GREEN}Web 访问:${NC} http://localhost:6080/vnc.html"
echo -e "${GREEN}API 地址:${NC} http://localhost:8888"
echo ""
echo -e "${GREEN}查看日志:${NC} cd ${INSTALL_DIR} && ${COMPOSE_CMD} logs -f"
echo -e "${GREEN}停止服务:${NC} cd ${INSTALL_DIR} && ${COMPOSE_CMD} down"
echo ""