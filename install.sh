#!/bin/bash
# Linux 微信支付回调系统一键安装脚本
# 用法：
#   curl ... | bash          -> 自动重新下载并进入交互菜单
#   bash install.sh           -> 交互菜单
#   bash install.sh install   -> 命令行模式

# ============================================================
# 管道检测：curl | bash 时 stdin 不是终端，重下脚本执行
# ============================================================
if [ ! -t 0 ]; then
    if [ -z "$1" ]; then
        curl -fsSL "https://raw.githubusercontent.com/Skylerboss/message_linux_wechat_pay/main/install.sh" -o /tmp/install.sh 2>/dev/null
        if [ -s /tmp/install.sh ]; then
            bash /tmp/install.sh
            rm -f /tmp/install.sh
        else
            echo "错误: 无法下载脚本，请手动运行:"
            echo "curl -fsSL https://raw.githubusercontent.com/Skylerboss/message_linux_wechat_pay/main/install.sh -o install.sh && bash install.sh"
        fi
        exit $?
    fi
fi

# ============================================================
# 配置
# ============================================================
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

VERSION="1.0.5"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

full_image="${REGISTRY}/${NAMESPACE}/${REPO_NAME}:latest"

COMPOSE_CMD="docker-compose"
if ! command -v docker-compose &> /dev/null; then
    docker compose version &> /dev/null 2>&1 && COMPOSE_CMD="docker compose"
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

    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
    echo -e "${GREEN}[OK] 安装完成 v${VERSION}${NC}"
    echo ""
    echo "VNC: vnc://${SERVER_IP}:${VNC_PORT}  密码: ${VNC_PASSWORD}"
    echo "Web: http://${SERVER_IP}:${NOVNC_PORT}/vnc.html"
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

# ============================================================
# 命令行参数模式
# ============================================================
if [ "$1" != "" ]; then
    case "$1" in
        1|install) install_service ;;
        2|stop)    stop_service ;;
        3|restart) restart_service ;;
        4|status)  view_status ;;
        5|logs)    view_logs ;;
        *) echo "用法: bash $0 [1-5|install|stop|restart|status|logs]" ;;
    esac
    exit 0
fi

# ============================================================
# 交互菜单模式
# ============================================================
while true; do
    echo ""
    echo -e "${CYAN}=== Linux 微信支付回调系统 v${VERSION} ===${NC}"
    echo ""
    echo "  1. 安装/更新服务"
    echo "  2. 停止服务"
    echo "  3. 重启服务"
    echo "  4. 查看状态"
    echo "  5. 查看日志"
    echo "  0. 退出"
    echo ""
    printf "请选择 [0-5]: "
    read choice
    case "$choice" in
        1) install_service ;;
        2) stop_service ;;
        3) restart_service ;;
        4) view_status ;;
        5) view_logs ;;
        0) echo "再见!"; exit 0 ;;
        *) echo -e "${RED}无效选择${NC}" ;;
    esac
    echo ""
    printf "按回车继续..."
    read dummy
done
