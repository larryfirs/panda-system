# 熊猫系统（Panda System）

单进程即可运行、内置 Web 管理界面的轻量任务与脚本管理面板。
本项目参考青龙面板实现，采用 **Python / FastAPI** 架构、可独立部署，覆盖六大核心板块：

| 板块 | 说明 |
|------|------|
| ⏰ 定时任务 | cron 表达式（5/6 段）、`@boot`/`@once`、任务视图、并发/排队、手动运行/停止、导入 crontab、任务前后置代码、多账号 `conc`/`desi` 模式、外部状态回调 |
| 📄 脚本管理 | 文件树、在线编辑、新建/重命名/移动/删除、同名自动备份、临时文件直接运行并实时推送输出（WebSocket） |
| 📜 日志管理 | 任务日志分块流式读取、运行中日志实时 tail、按文件名日期清理旧日志、存储保留与清理预览 |
| 🔍 对比工具 | 官方样本 vs 当前配置逐行高亮对比，支持保存（`config.sh` / `sendNotify.js` / `notify.py`） |
| 📦 依赖管理 | nodejs / python3 / linux 三类依赖，安装队列、实时日志、取消安装、强制重装、已安装状态探测 |
| 👤 用户管理 | 与青龙账号体系兼容（同格式 scrypt 密码）、多平台 token、两步验证(TOTP)、登录锁定、登录日志、IP 黑名单、头像、14 种通知渠道 |

任务解释器映射：`.py` → **本项目虚拟环境的 python**，`.js/.mjs/.cjs` → node，`.ts` → tsx/ts-node，`.sh` → bash，`.ps1` → powershell。
命令中可省略 `task`/`ql` 前缀，支持 `-m <秒>` 单任务超时。

## 目录结构

```
python_version/
├── main.py              # 启动入口（uvicorn）
├── requirements.txt     # Python 依赖
├── Dockerfile           # Docker 镜像构建
├── docker-compose.yml   # Docker Compose 部署
├── panda/               # 后端（FastAPI）
│   ├── app.py           # 应用工厂、鉴权中间件、WebSocket、静态资源
│   ├── config.py        # 路径与环境变量
│   ├── models.py / db.py
│   ├── scheduler.py     # APScheduler 定时调度
│   ├── executor.py      # 任务执行引擎（进程树管理、conc/desi、超时）
│   ├── envsync.py       # 环境变量同步为 env.sh / env.js / env.py
│   ├── notify.py        # 通知渠道
│   ├── security.py      # JWT / scrypt 密码 / TOTP
│   ├── ws.py            # WebSocket 广播
│   └── routers/         # crons / scripts / logs / configs / dependencies / envs / user / dashboard / system / health
├── static/              # 免构建原生 JS 单页前端
├── samples/             # 官方配置样本
└── data/                # 运行时数据（脚本/配置/日志/SQLite），部署时整体迁移即可
```

## 环境要求

- Python **3.10+**（开发与测试基于 3.11）
- 可选：Node.js（运行 JS/TS 任务）、Git Bash（Windows 上运行 `.sh` 任务与 `task_before/after`）

## 一、Windows 部署

```powershell
cd E:\Python_Project\qinglong\python_version

# 1. 创建虚拟环境（项目内 .venv，任务脚本也将使用它）
py -3.11 -m venv .venv

# 2. 安装依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. 启动
.\.venv\Scripts\python.exe main.py
```

浏览器访问 <http://127.0.0.1:5700>，默认账号 `admin` / `admin`，首次登录后请立即修改密码。

> 说明：面板管理的 `.py` 任务固定使用 `.venv` 中的解释器；在“依赖管理”中安装的
> python3 依赖也会直接装入这个虚拟环境，无需任何额外配置。

## 二、Linux 部署

```bash
# 建议放置于 /opt/panda
git clone <仓库地址> && cd <仓库>/python_version    # 或直接拷贝 python_version 目录

python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt

# 前台试运行
./.venv/bin/python main.py
```

### systemd 常驻服务（推荐）

```ini
# /etc/systemd/system/panda.service
[Unit]
Description=Panda System
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/panda/python_version
Environment=JWT_SECRET=请改为随机长字符串
Environment=PD_PORT=5700
ExecStart=/opt/panda/python_version/.venv/bin/python main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now panda
```

Linux 上如需管理 `linux` 类型依赖（apt/apk），请以有 root 权限的用户运行或配置 sudo 免密。

### 宝塔面板（BT Panel / Ubuntu Server）部署

> 适用：Ubuntu 22.04 / 24.04 + 已安装宝塔面板。核心思路：**代码放站点目录 → 建虚拟环境装依赖 → 用 Supervisor 常驻 → Nginx 反向代理（带 WebSocket 头）对外**。
> 宝塔界面版本较多，下面「文件 + 终端 + Supervisor + 反向代理」这条路线最通用、与面板版本无关，推荐照它做。

#### 步骤 0：装 Python 3.11（若系统自带 ≥3.10 可跳过）

面板「软件商店」搜 **“Python项目管理器”** 安装（可视化管理多版本 Python 与虚拟环境）；或直接在面板「终端」里 apt：

```bash
# Ubuntu 24.04 自带 Python 3.12，满足要求，可跳过装 3.11
# Ubuntu 22.04 默认 3.10（也能跑）；想要 3.11 用 deadsnakes：
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip
# 时区（任务按本地时间触发）
sudo timedatectl set-timezone Asia/Shanghai
```

如要运行 `.js/.ts` 任务：软件商店 → **“Node.js版本管理器”** → 安装 Node 20。

#### 步骤 1：上传代码到站点目录

面板「文件」→ 进入 `/www/wwwroot/` → 新建目录 `panda` → 把整个 `python_version` 目录上传/解压进去，最终路径：

```
/www/wwwroot/panda/python_version
```

（也可在「终端」用 `git clone` 后再 `mv` 到该路径。）

#### 步骤 2：建虚拟环境并安装依赖（面板「终端」）

```bash
cd /www/wwwroot/panda/python_version
python3.11 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
# 国内服务器 pip 慢可加镜像： -i https://pypi.tuna.tsinghua.edu.cn/simple
```

#### 步骤 3：前台先试跑一次，确认能起来

```bash
cd /www/wwwroot/panda/python_version
JWT_SECRET=改成一段随机长字符串 ./.venv/bin/python main.py
```

浏览器打开 `http://服务器公网IP:5700`，能看到登录页即成功（默认 `admin`/`admin`，登录后立即改密）。`Ctrl+C` 停掉，下一步做常驻。

#### 步骤 4：用 Supervisor 常驻（推荐）

软件商店 → 安装 **“Supervisor管理器”** → 「添加守护进程」：

| 项 | 值 |
|----|----|
| 进程名称 | `panda` |
| 启动命令 | `/www/wwwroot/panda/python_version/.venv/bin/python main.py` |
| 运行目录 | `/www/wwwroot/panda/python_version` |
| 进程数 | `1` |
| 环境变量 | `JWT_SECRET=改成随机长字符串,PD_PORT=5700` |

保存后在列表点「启动」，状态变 green/running 即常驻成功；改代码后在这里点「重启」生效。

> 不想用 Supervisor 的，可改用 systemd（与面板版本无关）：把上文 `## 二、Linux 部署` 里的 `panda.service` 模板中 `WorkingDirectory` / `ExecStart` 改成 `/www/wwwroot/panda/python_version/...`，`sudo systemctl enable --now panda`。

> 用面板 **“Python项目管理器” 图形化托管**（可选替代 4）：网站 → Python项目 → 添加项目，项目路径填 `/www/wwwroot/panda/python_version`，Python 版本选 3.11 虚拟环境，框架选 **FastAPI / 其它**，启动方式填 `python main.py`（监听端口由程序读 `PD_PORT`，默认 5700）。部分面板版本对该模块的 ASGI 启动封装不一致，若图形化启动异常，一律以上面 Supervisor 方式为准。

#### 步骤 5：放行端口

- 面板「安全」→「防火墙」→ 放行 `5700`（仅当你想直接 `IP:5700` 访问时需要；走域名反代则只放行 `80`/`443`）。
- 云服务器（阿里云/腾讯云等）还要在 **云厂商安全组** 同步放行对应端口。

#### 步骤 6：Nginx 反向代理 + 域名（生产推荐）

1. 面板「网站」→「添加站点」→ 填域名（如 `panda.example.com`），**不建数据库**、纯静态即可。
2. 站点建好 → 点「设置」→「反向代理」→「添加反向代理」：
   - 目标 URL：`http://127.0.0.1:5700`
   - 发送域名：`$host`
3. **关键：WebSocket 头**。脚本在线运行的实时输出、任务日志的实时 tail 都走 WebSocket，宝塔默认反代可能缺少升级头，需在反代配置文件里确保包含：

```nginx
proxy_pass http://127.0.0.1:5700;
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_read_timeout 3600s;   # 长任务/长连接不被切断
```

保存后若日志不实时刷新，多半就是缺 `Upgrade`/`Connection` 这两行。

4. 「SSL」→ Let's Encrypt 申请证书 → 打开「强制 HTTPS」。

#### 步骤 7（可选）：子路径部署

若要挂到 `https://域名/panda/`，给进程加环境变量 `PD_BASE_URL=/panda`，反代写成 `location /panda/ { proxy_pass http://127.0.0.1:5700/; }`（注意结尾斜杠）。独立域名或 `IP:5700` 直连最省事，非必要不建议子路径。

#### 数据备份与迁移

`/www/wwwroot/panda/python_version/data/` 内含脚本、配置、日志、SQLite 数据库，**打包该目录即可整体迁移/备份**。面板「计划任务」可设每天压缩备份 `data/`。

#### 宝塔环境注意事项

- **依赖管理板块**里安装 `linux` 类型依赖走 `apt`，需要 root；用宝塔部署时若进程用户为 `www`，请在面板以 root 运行或为 `apt` 配置 sudo 免密。`python3` 类型依赖会准确装进本项目的 `.venv`，`.py` 任务也固定用这个 `.venv`，无需额外配置。
- **忘记账号 / 被锁**：连续登录失败会按 `3^重试次数` 秒锁定，等待自动解锁即可。
- **改了端口/密钥**：修改 Supervisor（或 systemd）里的环境变量后，重启该守护进程生效。

## 三、Docker 部署

镜像内含 Python 3.11 + Node 20 + tsx/pnpm，`./data` 目录挂载后所有脚本、配置、
数据库可整体迁移。

### docker compose（推荐）

```bash
mkdir -p panda/data && cd panda
# 拷贝 docker-compose.yml 与项目文件（或 git clone）
docker compose up -d --build
```

### 纯 docker 命令

```bash
docker build -t panda-system .
docker run -d --name panda -p 5700:5700 \
  -v $(pwd)/data:/app/data \
  -e JWT_SECRET=请改为随机长字符串 \
  panda-system
```

访问 <http://宿主机IP:5700>。容器内任务日志、脚本、SQLite 均位于挂载的 `data/` 下。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PD_PORT`（或 `BACK_PORT`） | `5700` | 服务端口 |
| `PD_HOST` | `0.0.0.0` | 监听地址 |
| `PD_DIR` | 项目目录 | 安装根目录 |
| `PD_DATA_DIR` | `PD_DIR/data` | 数据目录（脚本/配置/日志/数据库） |
| `JWT_SECRET` | `panda-secret` | **生产环境必须修改**，令牌签名密钥 |
| `JWT_EXPIRES_IN` | 自动（20d/60d） | 令牌有效期，如 `20d` |
| `PD_BASE_URL` | 空 | 子路径部署前缀，如 `/panda` |
| `PD_PYTHON` | 虚拟环境 python | `.py` 任务使用的解释器 |
| `PD_DATABASE_URL` | SQLite | 可换成其他 SQLAlchemy 支持的数据库 |

## 与青龙面板的兼容性（可选迁移）

- 密码格式与青龙一致（`scrypt$...`），旧面板账号数据可迁移；明文密码首次登录校验成功后自动升级为 scrypt。
- 环境变量保存后自动生成 `config/env.sh`、`env.js`、`env.py`（同名多值以 `&` 连接），并通过
  `PYTHONPATH` 预加载注入 Python 任务，语义与青龙一致。
- 外部状态回调接口 `PUT /api/crons/status`、仪表盘记录 `POST /api/dashboard/record` 与青龙契约相同。
- 任务命令兼容 `task / ql` 前缀、`now`、`conc ENV 1-max`、`desi ENV 1,3`、`-m 超时` 等写法。

## 冒烟自检

服务启动后可运行端到端自检（覆盖登录、任务、脚本、日志、对比、依赖、环境、仪表盘、系统设置）：

```bash
PYTHONIOENCODING=utf-8 ./.venv/bin/python smoke_test.py   # Windows: .venv\Scripts\python.exe
```

## 常见问题

- **Windows 上 `.sh` 任务不执行**：安装 Git for Windows 并将 `bash` 加入 PATH。
- **忘记登录被锁**：连续失败 3 次后按 `3^重试次数` 秒锁定，等待解锁或删除 `data/db/database.sqlite` 中 `auths` 行（谨慎）。
- **换端口/HTTPS**：面板只监听 HTTP，HTTPS 与子路径反代请在 Nginx/Caddy 层处理并设置 `PD_BASE_URL`。
