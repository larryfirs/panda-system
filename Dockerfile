FROM python:3.11-slim

# 基础工具：curl(健康检查) git(repo 拉取) bash shell 任务；nodejs: JS/TS 任务
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git bash ca-certificates xz-utils \
    && rm -rf /var/lib/apt/lists/*

# 安装 Node.js 20 LTS（执行 .js / .ts 任务）
RUN ARCH="$(dpkg --print-architecture)" \
    && case "$ARCH" in amd64) NODE_ARCH=x64;; arm64) NODE_ARCH=arm64;; *) NODE_ARCH=$ARCH;; esac \
    && curl -fsSL "https://nodejs.org/dist/v20.18.0/node-v20.18.0-linux-${NODE_ARCH}.tar.xz" -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && npm install -g tsx pnpm

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY panda ./panda
COPY static ./static
COPY samples ./samples

# 数据目录（脚本/配置/日志/数据库）挂载点
ENV PD_DIR=/app \
    PYTHONIOENCODING=utf-8 \
    PD_PORT=5700
VOLUME [ "/app/data" ]
EXPOSE 5700

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS http://127.0.0.1:5700/api/health || exit 1

CMD ["python", "main.py"]
