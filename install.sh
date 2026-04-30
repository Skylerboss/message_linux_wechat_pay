#!/bin/bash
# Linux 微信支付回调系统一键安装脚本

VERSION="1.0.4"

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
    echo -e "${BLUE}[1/5] 拉取镜像...${NC}"
    docker pull "${full_image}" 2>/dev/null || echo -e "${YELLOW}使用现有镜像${NC}"
    
    mkdir -p "${INSTALL_DIR}/logs" "${INSTALL_DIR}/data"

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

    cd "${INSTALL_DIR}" && ${COMPOSE_CMD} up -d
    echo "${VERSION}" > "${INSTALL_DIR}/.version"
    echo -e "${GREEN}[OK] 安装完成 v${VERSION}${NC}"
    echo ""
    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
    echo "VNC: vnc://${SERVER_IP}:${VNC_PORT} 密码: ${VNC_PASSWORD}"
    echo "Web: http://${SERVER_IP}:${NOVNC_PORT}/vnc.html"
    echo "API: http://${SERVER_IP}:${API_PORT}"
    echo "日志: ${INSTALL_DIR}/logs/"
}

stop_service() {
    cd "${INSTALL_DIR}" 2>/dev/null && ${COMPOSE_CMD} down
    echo -e "${GREEN}[OK] 已停止${NC}"
}

restart_service() {
    cd "${INSTALL_DIR}" 2>/dev/null && ${COMPOSE_CMD} restart
    echo -e "${GREEN}[OK] 已重启${NC}"
}

view_logs() {
    if [ -d "${INSTALL_DIR}/logs" ]; then
        tail -30 "${INSTALL_DIR}/logs/callback_err.log" 2>/dev/null || echo "暂无日志"
    else
        echo -e "${RED}日志目录不存在${NC}"
    fi
}

view_status() {
    if docker ps 2>/dev/null | grep -q linux-wechat-pay; then
        echo -e "${GREEN}[OK] 运行中${NC}"
    else
        echo -e "${RED}[X] 未运行${NC}"
    fi
}

# 显示帮助
if [ "$1" = "" ]; then
    echo ""
    echo -e "${CYAN}=== Linux 微信支付回调系统 v${VERSION} ===${NC}"
    echo ""
    echo "用法: bash install.sh [命令]"
    echo ""
    echo "命令："
    echo "  1, install   安装/更新服务"
    echo "  2, stop      停止服务"
    echo "  3, restart   重启服务"
    echo "  4, status    查看状态"
    echo "  5, logs      查看日志"
    echo ""
    echo "示例："
    echo "  bash install.sh install"
    echo "  bash install.sh 1"
    echo ""
    echo "一键安装："
    echo "  curl -fsSL https://raw.githubusercontent.com/Skylerboss/message_linux_wechat_pay/main/install.sh | bash -s install"
    echo ""
    exit 0
fi

case "$1" in
    1|install)   install_service ;;
    2|stop)      stop_service ;;
    3|restart)    restart_service ;;
    4|status)    view_status ;;
    5|logs)      view_logs ;;
    *)          echo "无效命令: $1"; echo "使用 bash install.sh 查看帮助" ;;
esac
