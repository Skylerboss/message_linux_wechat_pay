FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libglib2.0-0 \
    libgl1-mesa-glx \
    libgtk-3-0 \
    libnotify-dev \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    tesseract-ocr-eng \
    dbus \
    libdbus-1-dev \
    libgirepository1.0-dev \
    libcairo2-dev \
    pkg-config \
    python3-dbus \
    python3-gi \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建数据目录
RUN mkdir -p /app/data

# 暴露端口
EXPOSE 8888 8889

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:0
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

# 启动命令
CMD ["python", "main.py", "-c", "/app/config.yaml"]
