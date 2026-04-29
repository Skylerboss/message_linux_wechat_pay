#!/bin/bash
# -*- coding: utf-8 -*-
# Linux 微信支付回调系统一键安装脚本
# 用法: curl -fsSL https://raw.githubusercontent.com/Skylerboss/message_linux_wechat_pay/main/install.sh | bash
# 或: bash install.sh

set -e

# 配置（可通过环境变量覆盖）
NAMESPACE="${WECHAT_NAMESPACE:-skylerboss}"
REPO_NAME="${WECHAT_REPO:-message_linux_wechat_pay}"
REGISTRY="${WECHAT_REGISTRY:-registry.cn-hangzhou.aliyuncs.com}"
INSTALL_DIR="${WECHAT_INSTALL_DIR:-/root/linux_wechat_pay}"

# Message Bot 配置
MESSAGE_BOT_URL="${MESSAGE_BOT_URL:-http://192.168.100.7:5000}"
CALLBACK_SECRET="${CALLBACK_SECRET:-}"

# Docker Compose 命令（全局变量）
COMPOSE_CMD=""

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# 版本
VERSION="1.0.0"

# 检查Docker
check_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}❌ Docker 未安装${NC}"
        echo "请先安装 Docker:"
        echo "  curl -fsSL https://get.docker.com | bash"
        exit 1
    fi
    
    # 检查 docker compose（兼容新旧版本）
    if command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    elif docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
    else
        echo -e "${RED}❌ Docker Compose 未安装${NC}"
        echo "请先安装 Docker Compose:"
        echo "  apt update && apt install -y docker-compose-plugin"
        echo "或者安装 docker-compose:"
        echo "  pip3 install docker-compose"
        exit 1
    fi
    
    if ! docker info &> /dev/null; then
        echo -e "${RED}❌ Docker 服务未运行${NC}"
        echo "请启动 Docker:"
        echo "  systemctl start docker"
        exit 1
    fi
    
    echo -e "${GREEN}✅ Docker 环境正常${NC}"
}

# 获取本地版本
get_local_version() {
    if [ -f "${INSTALL_DIR}/.version" ]; then
        cat "${INSTALL_DIR}/.version"
    else
        echo "none"
    fi
}

# 保存版本
save_version() {
    echo "$1" > "${INSTALL_DIR}/.version"
}

# 拉取镜像
pull_image() {
    local full_image="${REGISTRY}/${NAMESPACE}/${REPO_NAME}:latest"
    
    echo -e "${BLUE}📥 拉取镜像: ${full_image}${NC}"
    
    if docker pull "${full_image}"; then
        echo -e "${GREEN}✅ 镜像拉取成功${NC}"
        return 0
    else
        echo -e "${RED}❌ 镜像拉取失败${NC}"
        return 1
    fi
}

# 创建目录
setup_directories() {
    echo -e "${BLUE}📁 设置安装目录: ${INSTALL_DIR}${NC}"
    mkdir -p "${INSTALL_DIR}"
    mkdir -p "${INSTALL_DIR}/wechat-decrypt"
    echo -e "${GREEN}✅ 目录创建完成${NC}"
}

# 创建环境变量文件
create_env_file() {
    echo -e "${BLUE}📝 创建 .env 配置文件${NC}"
    
    cat > "${INSTALL_DIR}/.env" << EOF
# Linux 微信支付回调配置

# 回调地址（设置为 Message Bot 的地址）
CALLBACK_URL=${MESSAGE_BOT_URL}/api/payment/notify/linux_wechat

# 回调密钥（可选，用于签名验证）
CALLBACK_SECRET_KEY=${CALLBACK_SECRET}

# 解密项目目录
DECRYPT_PROJECT_DIR=/root/wechat-decrypt
EOF
    
    echo -e "${GREEN}✅ 配置文件创建完成${NC}"
}

# 创建 docker-compose.yml
create_compose_file() {
    local full_image="${REGISTRY}/${NAMESPACE}/${REPO_NAME}:latest"
    
    echo -e "${BLUE}📝 创建 docker-compose.yml${NC}"
    
    cat > "${INSTALL_DIR}/docker-compose.yml" << EOF
version: '3.8'

services:
  linux-wechat-pay:
    image: ${full_image}
    container_name: linux-wechat-pay
    privileged: true
    restart: unless-stopped
    ports:
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
      - wechat_net

networks:
  wechat_net:
    driver: bridge
EOF
    
    echo -e "${GREEN}✅ docker-compose.yml 创建完成${NC}"
}

# 启动服务
start_service() {
    local compose_cmd=$1
    
    echo -e "${BLUE}🚀 启动服务...${NC}"
    
    cd "${INSTALL_DIR}"
    
    if ${compose_cmd} up -d; then
        echo -e "${GREEN}✅ 服务启动成功${NC}"
        return 0
    else
        echo -e "${RED}❌ 服务启动失败${NC}"
        return 1
    fi
}

# 显示状态
show_status() {
    local compose_cmd=$1
    
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Linux 微信支付回调系统 安装完成${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
    echo -e "📦 镜像: ${REGISTRY}/${NAMESPACE}/${REPO_NAME}"
    echo -e "📁 安装目录: ${INSTALL_DIR}"
    echo -e "🌐 服务端口: 8888"
    echo ""
    echo -e "${GREEN}查看日志:${NC}"
    echo "  cd ${INSTALL_DIR} && ${compose_cmd} logs -f"
    echo ""
    echo -e "${GREEN}停止服务:${NC}"
    echo "  cd ${INSTALL_DIR} && ${compose_cmd} down"
    echo ""
    echo -e "${GREEN}重启服务:${NC}"
    echo "  cd ${INSTALL_DIR} && ${compose_cmd} restart"
    echo ""
}

# 卸载服务
uninstall_service() {
    local compose_cmd=$1
    
    echo -e "${YELLOW}⚠️  确认卸载 Linux 微信支付回调系统？${NC}"
    read -p "输入 'yes' 确认卸载: " confirm
    
    if [ "$confirm" = "yes" ]; then
        echo -e "${BLUE}🗑️  停止并删除容器...${NC}"
        cd "${INSTALL_DIR}"
        ${compose_cmd} down -v 2>/dev/null || true
        
        echo -e "${YELLOW}⚠️  是否删除数据目录？[y/N]:${NC}"
        read -p "确认删除所有数据: " delete_data
        if [ "$delete_data" = "y" ]; then
            rm -rf "${INSTALL_DIR}"
            echo -e "${GREEN}✅ 数据已删除${NC}"
        fi
        
        echo -e "${GREEN}✅ 卸载完成${NC}"
    else
        echo -e "${BLUE}已取消卸载${NC}"
    fi
}

# 主菜单
main_menu() {
    # 检测 docker compose 命令
    if command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    elif docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
    fi
    
    local current_version=$(get_local_version)
    
    while true; do
        echo ""
        echo -e "${CYAN}╔═══════════════════════════════════════════╗${NC}"
        echo -e "${CYAN}║   Linux 微信支付回调系统 v${VERSION}        ║${NC}"
        echo -e "${CYAN}╚═══════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "当前状态: ${GREEN}已安装 v${current_version}${NC}"
        echo ""
        echo "  1. 安装/更新服务"
        echo "  2. 修改配置"
        echo "  3. 查看服务状态"
        echo "  4. 查看日志"
        echo "  5. 重启服务"
        echo "  6. 停止服务"
        echo "  7. 卸载服务"
        echo "  0. 退出"
        echo ""
        read -p "请选择 [0-7]: " choice
        
        case $choice in
            1)
                check_docker
                pull_image
                setup_directories
                create_env_file
                create_compose_file
                start_service ${COMPOSE_CMD}
                save_version "${VERSION}"
                current_version="${VERSION}"
                show_status ${COMPOSE_CMD}
                ;;
            2)
                if [ -f "${INSTALL_DIR}/.env" ]; then
                    echo -e "${BLUE}📝 当前配置:${NC}"
                    cat "${INSTALL_DIR}/.env"
                    echo ""
                    read -p "按回车继续修改配置... "
                    vi "${INSTALL_DIR}/.env"
                    echo -e "${GREEN}✅ 配置已更新，重启后生效${NC}"
                else
                    echo -e "${RED}未安装，请先选择 1 进行安装${NC}"
                fi
                ;;
            3)
                cd "${INSTALL_DIR}" && ${COMPOSE_CMD} ps
                ;;
            4)
                cd "${INSTALL_DIR}" && ${COMPOSE_CMD} logs -f --tail=50
                ;;
            5)
                cd "${INSTALL_DIR}" && ${COMPOSE_CMD} restart
                echo -e "${GREEN}✅ 服务已重启${NC}"
                ;;
            6)
                cd "${INSTALL_DIR}" && ${COMPOSE_CMD} stop
                echo -e "${GREEN}✅ 服务已停止${NC}"
                ;;
            7)
                uninstall_service ${COMPOSE_CMD}
                current_version="none"
                ;;
            0)
                echo -e "${GREEN}再见！${NC}"
                exit 0
                ;;
            *)
                echo -e "${RED}无效选择，请重新输入${NC}"
                ;;
        esac
    done
}

# 检查是否已安装
if [ -d "${INSTALL_DIR}" ] && [ -f "${INSTALL_DIR}/.version" ]; then
    main_menu
else
    # 首次安装
    check_docker
    pull_image
    setup_directories
    create_env_file
    create_compose_file
    
    start_service ${COMPOSE_CMD}
    save_version "${VERSION}"
    show_status ${COMPOSE_CMD}
fi