#!/bin/bash
# -*- coding: utf-8 -*-
# Linux 微信支付回调系统一键安装脚本

# 配置
NAMESPACE="${WECHAT_NAMESPACE:-skylerboss}"
REPO_NAME="${WECHAT_REPO:-message_linux_wechat_pay}"
REGISTRY="${WECHAT_REGISTRY:-registry.cn-hangzhou.aliyuncs.com}"
INSTALL_DIR="${WECHAT_INSTALL_DIR:-/root/linux_wechat_pay}"
MESSAGE_BOT_URL="${MESSAGE_BOT_URL:-http://192.168.100.7:5000}"
CALLBACK_SECRET="${CALLBACK_SECRET:-}"

VNC_PORT="${VNC_PORT:-5901}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
API_PORT="${API_PORT:-8888}"
VNC_PASSWORD="${VNC_PASSWORD:-wechat123}"

VERSION="1.0.2"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

full_image="${REGISTRY}/${NAMESPACE}/${REPO_NAME}:latest"

COMPOSE_CMD="docker-compose"
if ! command -v docker-compose &> /dev/null; then
    if docker compose version &> /dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
    fi
fi

install_service() {
    echo -e "${CYAN}╔═══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║   Linux 微信支付回调系统 v${VERSION}        ║${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════╝${NC}"
    echo ""

    echo -e "${BLUE}📥 拉取镜像: ${full_image}${NC}"
    docker pull "${full_image}" 2>/dev/null || echo -e "${YELLOW}使用现有镜像${NC}"
    echo -e "${GREEN}✅ 镜像拉取成功${NC}"

    mkdir -p "${INSTALL_DIR}/logs"
    mkdir -p "${INSTALL_DIR}/data"

    cat > "${INSTALL_DIR}/.env" << ENVEOF
CALLBACK_URL=${MESSAGE_BOT_URL}/api/payment/notify/linux_wechat
CALLBACK_SECRET_KEY=${CALLBACK_SECRET}
DECRYPT_PROJECT_DIR=/app/wechat-decrypt
SESSION_DB_PATH=/app/wechat-decrypt/decrypted/session/session.db
LOG_LEVEL=INFO
ENVEOF

    cat > "${INSTALL_DIR}/docker-compose.yml" << COMPOSEEOF
version: '3.8'

services:
  linux-wechat-pay:
    image: ${full_image}
    container_name: linux-wechat-pay
    privileged: true
    restart: unless-stopped
    ports:
      - "${VNC_PORT}:5901"
      - "${NOVNC_PORT}:6080"
      - "${API_PORT}:8888"
    volumes:
      - /root/Documents:/root/Documents
      - ./logs:/var/log/supervisor
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

    cd "${INSTALL_DIR}"
    ${COMPOSE_CMD} up -d
    echo "${VERSION}" > "${INSTALL_DIR}/.version"

    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
    echo ""
    echo -e "${GREEN}✅ 服务启动成功${NC}"
    echo ""
    echo -e "📁 安装目录: ${INSTALL_DIR}"
    echo -e "${GREEN}VNC 访问:${NC} vnc://${SERVER_IP}:${VNC_PORT}"
    echo -e "${GREEN}Web 访问:${NC} http://${SERVER_IP}:${NOVNC_PORT}/vnc.html"
    echo -e "${GREEN}日志目录:${NC} ${INSTALL_DIR}/logs"
}

stop_service() {
    cd "${INSTALL_DIR}" 2>/dev/null && ${COMPOSE_CMD} down
    echo -e "${GREEN}✅ 服务已停止${NC}"
}

restart_service() {
    cd "${INSTALL_DIR}" 2>/dev/null && ${COMPOSE_CMD} restart
    echo -e "${GREEN}✅ 服务已重启${NC}"
}

view_logs() {
    if [ -d "${INSTALL_DIR}/logs" ]; then
        echo -e "${CYAN}=== 日志目录: ${INSTALL_DIR}/logs ===${NC}"
        ls -la "${INSTALL_DIR}/logs/"
        echo ""
        echo -e "${CYAN}=== callback_err.log ===${NC}"
        tail -30 "${INSTALL_DIR}/logs/callback_err.log" 2>/dev/null || echo "暂无日志"
    else
        echo -e "${RED}日志目录不存在${NC}"
    fi
}

view_status() {
    if docker ps 2>/dev/null | grep -q linux-wechat-pay; then
        echo -e "${GREEN}● 服务状态: 运行中${NC}"
        docker ps --filter "name=linux-wechat-pay" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    else
        echo -e "${RED}● 服务状态: 未运行${NC}"
    fi
}

show_menu() {
    echo ""
    echo -e "${CYAN}╔═══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║   Linux 微信支付回调系统 v${VERSION}        ║${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${GREEN}1${NC}. 安装/更新服务"
    echo -e "  ${GREEN}2${NC}. 停止服务"
    echo -e "  ${GREEN}3${NC}. 重启服务"
    echo -e "  ${GREEN}4${NC}. 查看状态"
    echo -e "  ${GREEN}5${NC}. 查看日志"
    echo -e "  ${GREEN}6${NC}. 访问 VNC"
    echo -e "  ${GREEN}0${NC}. 退出"
    echo ""
    echo -n "请选择操作 [0-6]: "
}

if [ "$1" != "" ]; then
    case "$1" in
        1|install) install_service ;;
        2|stop) stop_service ;;
        3|restart) restart_service ;;
        4|status) view_status ;;
        5|logs) view_logs ;;
        *) echo "用法: $0 或 $0 {1-6}" ;;
    esac
    exit 0
fi

while true; do
    show_menu
    read choice
    case "$choice" in
        1) install_service ;;
        2) stop_service ;;
        3) restart_service ;;
        4) view_status ;;
        5) view_logs ;;
        6)
            SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
            echo "VNC 地址: http://${SERVER_IP}:${NOVNC_PORT}/vnc.html"
            ;;
        0) echo "再见!"; exit 0 ;;
        *) echo "无效选择，请重试" ;;
    esac
done