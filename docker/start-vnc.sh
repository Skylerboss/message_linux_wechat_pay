#!/bin/bash
# VNC Docker容器启动脚本
# 初始化环境并启动所有服务

# =============================================================================
# 配置变量
# =============================================================================
VNC_PORT=${VNC_PORT:-5901}
NOVNC_PORT=${NOVNC_PORT:-6080}
VNC_RESOLUTION=${VNC_RESOLUTION:-1280x800}
VNC_COL_DEPTH=${VNC_COL_DEPTH:-24}
VNC_PW=${VNC_PW:-wechat123}
DISPLAY_NUM=1

# =============================================================================
# 设置VNC密码
# =============================================================================
echo "Setting VNC password..."
mkdir -p /root/.vnc
echo "$VNC_PW" | vncpasswd -f > /root/.vnc/passwd
chmod 600 /root/.vnc/passwd

# 确保xstartup文件有执行权限
if [ -f /root/.vnc/xstartup ]; then
    chmod +x /root/.vnc/xstartup
    echo "xstartup file OK"
else
    echo "ERROR: xstartup file not found!"
    exit 1
fi

# =============================================================================
# 清理旧VNC会话
# =============================================================================
echo "Cleaning up old VNC sessions..."
vncserver -kill :${DISPLAY_NUM} 2>/dev/null || true
rm -f /tmp/.X${DISPLAY_NUM}-lock /tmp/.X11-unix/X${DISPLAY_NUM} 2>/dev/null || true

# =============================================================================
# 生成Xauthority文件
# =============================================================================
echo "Generating Xauthority file..."
mcookie | sed -e "s/^/add :${DISPLAY_NUM} MIT-MAGIC-COOKIE-1 /" -e 's/$/ 1000/' > /root/.Xauthority 2>/dev/null || true
chown root:root /root/.Xauthority 2>/dev/null || true
chmod 600 /root/.Xauthority 2>/dev/null || true

# =============================================================================
# 启动VNC服务器（使用Xvnc直接启动，更可靠）
# =============================================================================
echo "Starting Xvnc on display :${DISPLAY_NUM} port ${VNC_PORT}..."
echo "Resolution: ${VNC_RESOLUTION}, Color depth: ${VNC_COL_DEPTH}"

Xvnc :${DISPLAY_NUM} \
    -depth ${VNC_COL_DEPTH} \
    -geometry ${VNC_RESOLUTION} \
    -rfbport ${VNC_PORT} \
    -rfbauth /root/.vnc/passwd \
    -SecurityTypes VncAuth \
    -AlwaysShared \
    -NeverShared=false \
    -localhost no \
    -desktop wechat-vnc &
XVNC_PID=$!

# 等待X服务器启动
sleep 2

# 检查Xvnc是否运行
if ! kill -0 ${XVNC_PID} 2>/dev/null; then
    echo "ERROR: Xvnc failed to start!"
    exit 1
fi

echo "Xvnc started successfully (PID: ${XVNC_PID})"

# =============================================================================
# 启动桌面环境（通过xstartup脚本）
# =============================================================================
export DISPLAY=:${DISPLAY_NUM}

echo "Starting desktop environment via xstartup..."
/root/.vnc/xstartup &
XSTARTUP_PID=$!

# 等待桌面启动
sleep 3

echo "VNC server is running on port ${VNC_PORT}"
echo "VNC Password: ${VNC_PW}"

# =============================================================================
# 启动noVNC（websockify）- 可选
# =============================================================================
if [ -x /opt/noVNC/utils/websockify/run ]; then
    echo "Starting noVNC on port ${NOVNC_PORT}..."
    /opt/noVNC/utils/websockify/run \
        --web /opt/noVNC \
        --cert none \
        ${NOVNC_PORT} \
        localhost:${VNC_PORT} &
    sleep 1
    echo "noVNC started on port ${NOVNC_PORT}"
fi

# =============================================================================
# 配置微信支付回调服务
# =============================================================================
echo "Configuring WeChat payment callback service..."
mkdir -p /app/data

if [ ! -f /app/config.yaml ]; then
    cp /app/config.example.yaml /app/config.yaml 2>/dev/null || true
fi

# 使用 sed 替换环境变量
echo "CALLBACK_URL=$CALLBACK_URL"
echo "CALLBACK_SECRET_KEY=$CALLBACK_SECRET_KEY"

if [ -n "$CALLBACK_URL" ]; then
    echo "Updating CALLBACK_URL in config.yaml..."
    sed -i "s|\${CALLBACK_URL}|$CALLBACK_URL|g" /app/config.yaml
    echo "CALLBACK_URL updated"
fi

if [ -n "$CALLBACK_SECRET_KEY" ]; then
    echo "Updating CALLBACK_SECRET_KEY in config.yaml..."
    sed -i "s|\${CALLBACK_SECRET_KEY}|$CALLBACK_SECRET_KEY|g" /app/config.yaml
    echo "CALLBACK_SECRET_KEY updated"
fi

# 修复所有路径配置
echo "Fixing all paths in config.yaml..."

# 修复 SESSION_DB_PATH
sed -i 's|session_db_path:.*|session_db_path: "/app/wechat-decrypt/decrypted/session/session.db"|g' /app/config.yaml

# 修复 DECRYPT_PROJECT_DIR
sed -i 's|decrypt_project_dir:.*|decrypt_project_dir: "/app/wechat-decrypt"|g' /app/config.yaml

# 修复回调URL
if [ -n "$CALLBACK_URL" ]; then
    sed -i "s|url: \"\${CALLBACK_URL}|url: \"${CALLBACK_URL}|g" /app/config.yaml
fi

# 修复回调密钥
if [ -n "$CALLBACK_SECRET_KEY" ]; then
    sed -i "s|secret_key: \"\${CALLBACK_SECRET_KEY}|secret_key: \"${CALLBACK_SECRET_KEY}|g" /app/config.yaml
fi

echo "All paths fixed in config.yaml"

# 自动检测微信数据库路径（支持通配符匹配不同用户ID）
echo "Detecting WeChat database path..."
WECHAT_DB_DIR=$(find /root/Documents -type d -path "*/xwechat_files/*/db_storage" 2>/dev/null | head -1)
if [ -n "$WECHAT_DB_DIR" ]; then
    echo "Found WeChat database directory: $WECHAT_DB_DIR"
    export WECHAT_DB_DIR
    export SESSION_DB_PATH="${DECRYPT_PROJECT_DIR:-/app/wechat-decrypt}/decrypted/session/session.db"
    
    # 配置 wechat-decrypt
    if [ -f "${DECRYPT_PROJECT_DIR:-/app/wechat-decrypt}/config.json" ]; then
        echo "Updating wechat-decrypt config..."
        # 使用 sed 更新 db_dir
        sed -i "s|\"db_dir\": \".*\"|\"db_dir\": \"$WECHAT_DB_DIR\"|g" "${DECRYPT_PROJECT_DIR:-/app/wechat-decrypt}/config.json"
    fi
    
    # 如果微信进程运行中，自动提取密钥和解密
    if pgrep -x wechat > /dev/null; then
        echo "WeChat process detected, extracting database keys..."
        cd "${DECRYPT_PROJECT_DIR:-/app/wechat-decrypt}" && python3 find_all_keys_linux.py 2>&1 | tail -5
        
        echo "Decrypting databases..."
        cd "${DECRYPT_PROJECT_DIR:-/app/wechat-decrypt}" && python3 decrypt_db.py 2>&1 | tail -3
    else
        echo "WeChat not running, will decrypt after login"
    fi
else
    echo "Warning: WeChat database directory not found. Will configure after WeChat login."
    export WECHAT_DB_DIR=""
    export SESSION_DB_PATH=""
fi

export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket

# =============================================================================
# =============================================================================
# 启动微信客户端（后台运行）
# =============================================================================
echo "Starting WeChat client..."
/opt/wechat/wechat > /dev/null 2>&1 &

# =============================================================================
# 后台监控：持续检测微信登录状态并自动解密
# =============================================================================
(
    # 强制使用正确路径
    DECRYPT_DIR="/app/wechat-decrypt"
    DECRYPTED_DIR="${DECRYPT_DIR}/decrypted"
    LAST_SCAN_TIME=0
    
    # 日志输出到文件和控制台
    exec > >(tee -a /tmp/auto_decrypt.log) 2>&1
    
    echo "[auto-decrypt] 后台监控已启动 (PID: $$)"
    
    while true; do
        sleep 10
        
        # 检查微信是否运行
        if ! pgrep -f "/opt/wechat/wechat" > /dev/null; then
            continue
        fi
        
        # 检查微信是否已登录（通过数据库目录）
        WECHAT_DB_DIR=$(find /root/Documents -type d -name "db_storage" 2>/dev/null | head -1)
        
        if [ -z "$WECHAT_DB_DIR" ]; then
            # 列出所有可能的目录
            echo "[auto-decrypt] 等待微信登录... dirs: $(ls -la /root/Documents/xwechat_files/ 2>/dev/null | head -3)"
            continue
        fi
        
        echo "[auto-decrypt] 检测到微信数据库: $WECHAT_DB_DIR"
        
        # 检查是否需要重新解密（密钥文件为空或过旧）
        NEED_DECRYPT=false
        
        if [ ! -s "${DECRYPT_DIR}/all_keys.json" ]; then
            NEED_DECRYPT=true
            echo "[auto-decrypt] 密钥文件不存在，需要扫描"
        else
            # 检查解密数据库是否有效
            if [ ! -f "${DECRYPTED_DIR}/session/session.db" ]; then
                NEED_DECRYPT=true
                echo "[auto-decrypt] 解密数据库不存在，需要解密"
            fi
        fi
        
        if [ "$NEED_DECRYPT" = "true" ]; then
            echo "[auto-decrypt] 开始扫描密钥和解密..."
            
            # 更新配置 - 修复路径
            if [ -f "${DECRYPT_DIR}/config.json" ]; then
                sed -i "s|\"db_dir\": \".*\"|\"db_dir\": \"$WECHAT_DB_DIR\"|g" "${DECRYPT_DIR}/config.json"
                # 修复路径前缀
                sed -i 's|/home/sky/Documents/xwechat_files|/root/Documents/xwechat_files|g' "${DECRYPT_DIR}/config.json"
            fi
            
            # 扫描密钥
            cd "$DECRYPT_DIR"
            python3 find_all_keys_linux.py > /tmp/key_scan.log 2>&1
            
            if [ -s "${DECRYPT_DIR}/all_keys.json" ]; then
                # 解密数据库
                python3 decrypt_db.py > /tmp/decrypt.log 2>&1
                
                if [ -f "${DECRYPTED_DIR}/session/session.db" ]; then
                    echo "[auto-decrypt] 数据库解密成功!"
                    
                    # 同时修复 config.yaml 的路径
                    sed -i 's|/app/wechat-decrypt|/app/wechat-decrypt|g' /app/config.yaml
                    echo "[auto-decrypt] config.yaml 路径已修复"
                else
                    echo "[auto-decrypt] 解密失败，查看 /tmp/decrypt.log"
                fi
            else
                echo "[auto-decrypt] 密钥扫描失败"
            fi
        fi
    done
) &
AUTO_DECRYPT_PID=$!
echo "[auto-decrypt] 后台监控PID: $AUTO_DECRYPT_PID"

# =============================================================================
# 启动supervisord管理进程
# =============================================================================
echo ""
echo "==================================================================="
echo "Linux WeChat + VNC + Payment Callback Service Started"
echo "==================================================================="
echo "VNC Native Access:    vnc://localhost:${VNC_PORT} (Password: ${VNC_PW})"
echo "Browser Access:       http://localhost:${NOVNC_PORT}/vnc.html"
echo "Payment Callback API: http://localhost:8888"
echo "Health Check:         http://localhost:8888/health"
echo "==================================================================="
echo ""

# 启动supervisord作为主进程
exec supervisord -c /etc/supervisor/conf.d/supervisord.conf
