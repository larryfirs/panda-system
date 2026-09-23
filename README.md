# 熊猫系统（Panda System）

单进程即可运行、内置 Web 管理界面的轻量任务与脚本管理面板。
参考青龙面板实现，采用 **Python / FastAPI（ASGI）** 架构，面向 **Windows** 开发与运行。

功能覆盖六大板块：

| 板块 | 说明 |
|------|------|
| ⏰ 定时任务 | cron 表达式（5/6 段）、`@boot`/`@once`、任务视图、并发/排队、手动运行/停止、导入 crontab、任务前后置代码、多账号 `conc`/`desi` 模式、外部状态回调 |
| 📄 脚本管理 | 文件树、在线编辑、新建/重命名/移动/删除、同名自动备份、临时文件直接运行并实时推送输出（WebSocket） |
| 📜 日志管理 | 任务日志分块流式读取、运行中日志实时 tail、按文件名日期清理旧日志、存储保留与清理预览 |
| 🔍 对比工具 | 官方样本 vs 当前配置逐行高亮对比，支持保存（`config.sh` / `sendNotify.js` / `notify.py`） |
| 📦 依赖管理 | nodejs / python3 / linux 三类依赖，安装队列、实时日志、取消安装、强制重装、已安装状态探测 |
| 👤 用户管理 | 与青龙账号体系兼容（同格式 scrypt 密码）、多平台 token、两步验证(TOTP)、登录锁定、登录日志、IP 黑名单、头像、14 种通知渠道 |

---

## 一、30 秒看懂架构

一次请求在项目中的流转路径：

```
浏览器 (static/index.html + app.js)
   │  HTTP /api/... 与 WebSocket /api/ws
   ▼
app/main.py            ← 总装配：鉴权中间件、挂路由、WS、静态页
   ▼
app/routers/*          ← 接口层：只负责"收参数、查权限、拼响应"
   ▼
app/services/*         ← 业务层：调度、执行任务、发通知等真正的逻辑
   ▼
app/models/ + core/db  ← 数据层：SQLAlchemy ORM ↔ data/db/database.sqlite
```

配套约定：

- **core/** 是地基：路径配置、数据库连接、密码/JWT、统一响应格式，所有层都可以用它，它不用任何层。
- **响应格式统一**：所有接口经 `core/common.py` 的 `ok()/fail()` 返回 `{code, data, message}`，前端只需认这一种格式。
- **实时推送**：任务输出、依赖安装日志走 WebSocket，由 `services/ws.py` 的 `ws_manager` 单例广播。
- **单进程设计**：APScheduler 调度器是进程内单例，**不要**用多 worker 方式启动本服务。

## 二、文件树详解

```
panda-system/
├── main.py                    # 唯一启动入口。python main.py 即启动（内部调 uvicorn）
├── build.py                   # 打包脚本：python build.py → dist/panda-system_v<版本>_<时间>.tar.gz
│                              #   只含运行代码，解压后顶层目录固定为 panda-system/
├── requirements.txt           # Python 依赖清单（== 精确锁版本，与 Python 3.14 兼容验证一致）
├── smoke_test.py              # 端到端冒烟自检：服务启动后运行，40+ 项断言覆盖全部接口
│
├── app/                       # ── 后端代码包 ──────────────────────────────
│   ├── main.py                # 应用装配（create_app 工厂函数）：
│   │                          #   ① 鉴权中间件（白名单外的 /api/* 必须带 JWT）
│   │                          #   ② include 全部 routers  ③ /api/ws WebSocket 端点
│   │                          #   ④ 静态资源挂载与 SPA 首页  ⑤ 全局异常兜底
│   │
│   ├── core/                  # 基础设施层（被所有层引用，不引用任何业务层）
│   │   ├── config.py          #   全部路径常量与环境变量（PD_PORT、JWT_SECRET、数据目录…）
│   │   │                      #   免鉴权白名单 API_WHITE_LIST、默认账号也在这里
│   │   ├── db.py              #   SQLAlchemy engine / SessionLocal / Base，SQLite 连接事件
│   │   ├── security.py        #   JWT 签发校验、scrypt 密码哈希（兼容青龙格式）、TOTP 两步验证
│   │   ├── common.py          #   ok()/fail()/msg() 统一响应体，全项目接口出参都走它
│   │   └── bootstrap.py       #   启动引导：建目录、配日志轮转、建表播种(admin/admin)、
│   │                          #   释放样本文件、恢复未完成任务、启动调度器
│   │
│   ├── models/                # ORM 模型层（SQLAlchemy 2.0 声明式）
│   │   └── __init__.py        #   全部表定义：Crontab 定时任务、RunningInstance 运行实例、
│   │                          #   Env 环境变量、Dependency 依赖、SystemRow 系统配置、
│   │                          #   Auth 账号、任务视图/统计等，含状态常量
│   │
│   ├── routers/               # 接口层（FastAPI APIRouter，一个板块一个文件）
│   │   ├── __init__.py        #   汇总 all_routers 列表，供 app/main.py 统一挂载
│   │   ├── health.py          #   GET /api/health 存活探测
│   │   ├── user.py            #   登录/登出、两步验证、改密、头像、多平台 token、IP 黑名单
│   │   ├── cron.py            #   定时任务 CRUD、启停、立即运行、视图、导入 crontab、状态回调
│   │   ├── script.py          #   脚本文件树、在线编辑保存（同名自动备份）、临时运行
│   │   ├── log.py             #   日志树、分块读取、运行中日志 tail
│   │   ├── record.py          #   运行实例/历史记录的查询与删除
│   │   ├── dependence.py      #   依赖安装队列（nodejs/python3/linux 三类，后台线程消费）
│   │   ├── env.py             #   环境变量 CRUD，增删后触发同步生成 env.sh/js/py
│   │   ├── config_.py         #   配置文件对比（样本 vs 当前）与保存（文件名与 core.config
│   │   │                      #   冲突，故加下划线）
│   │   ├── system.py          #   系统信息、面板设置、日志清理策略、通知渠道测试
│   │   └── dashboard.py       #   仪表盘：任务总览、成功率趋势、运行时统计
│   │
│   ├── services/              # 业务服务层（不感知 HTTP，可被多个 router 复用）
│   │   ├── scheduler.py       #   PandaScheduler：包装 APScheduler，cron 校验/下次时间、
│   │   │                      #   @boot/@once 特殊调度、每日维护任务
│   │   ├── executor.py        #   任务执行引擎：拼命令行(task/ql 前缀、conc/desi)、起进程、
│   │   │                      #   进程树管理与强杀、超时(-m)、日志落盘、实例状态流转
│   │   ├── auth.py            #   登录态：token 校验、系统配置读取、登录失败锁定计数
│   │   ├── notify.py          #   14 种通知渠道（钉钉/飞书/Telegram/邮件/Bark…）
│   │   ├── envsync.py         #   环境变量 → env.sh/env.js/env.py + preload 注入文件
│   │   └── ws.py              #   ws_manager：WebSocket 连接池与线程安全广播
│   │
│   └── utils/                 # 通用工具（无业务语义）
│       └── __init__.py        #   安全路径拼接、文件大小格式化、进程树查找(psutil)、
│                              #   cron 字段解析、备份文件名等杂项函数
│
├── static/                    # 前端（免构建原生 JS 单页，无框架无 npm）
│   ├── index.html             #   页面骨架
│   └── assets/
│       ├── app.js             #   全部页面逻辑：路由、表格渲染、WS 订阅、请求封装
│       └── style.css          #   样式
│
├── samples/                   # 官方配置样本（对比工具的"标准答案"）
│   ├── config.sample.sh
│   ├── notify.js
│   └── notify.py
│
└── data/                      # 运行时数据（.gitignore 已忽略；整目录拷走即完成迁移）
    ├── scripts/               #   面板管理的脚本目录（任务的工作目录）
    ├── config/                #   env.sh / env.js / env.py / crontab.list / preload/ …
    ├── log/                   #   任务日志（按 <脚本名>_<id>/日期时间.log 存放）
    ├── db/database.sqlite     #   SQLite 数据库（所有表都在这一个文件里）
    ├── bak/  upload/  repo/   #   脚本备份 / 上传文件(头像) / git 仓库拉取目录
    └── syslog/                #   面板自身运行日志（panda.log，每日轮转保留 7 天）
```

## 三、当前架构 vs 原架构

| 维度 | 原架构（panda/） | 现架构（app/） |
|------|------------------|----------------|
| 包名 | `panda` | `app`（FastAPI 社区惯例） |
| 分层 | 扁平：路由和业务逻辑平铺在包根下，仅 `routers/` 一个子目录 | 四层：`core`(地基) / `models`(数据) / `routers`(接口) / `services`(业务) + `utils` |
| 依赖方向 | 互相随手 import（router 直接 import db、executor 又 import auth…成网状） | 单向：routers → services → models/core，core 不反向依赖 |
| 关键文件去向 | `panda/app.py` | `app/main.py`（应用装配） |
| | `panda/init.py` | `app/core/bootstrap.py`（名字更直白） |
| | `panda/common.py` | `app/core/common.py` |
| | `panda/models.py` 单文件 | `app/models/` 包 |
| | `panda/utils.py` 单文件 | `app/utils/` 包 |
| | `panda/scheduler.py、executor.py、auth.py、notify.py、envsync.py、ws.py` | `app/services/` 同名文件（纯业务，未改逻辑） |
| | `panda/routers/*` | `app/routers/*`（仅 import 路径变浅一层） |
| 部署形态 | Docker + 宝塔/Linux/systemd + Windows 混杂（Dockerfile、docker-compose.yml、build.py） | **只保留 Windows 直跑**，部署文件已删除 |
| 对外行为 | — | **完全不变**：API 路径、响应格式、data/ 目录结构、SQLite 数据全部兼容，旧 data/ 直接可用 |

一句话总结：这次重构只挪了"代码放在哪个文件夹"，没有改任何功能——所以冒烟测试在重构前后结果一致。

## 四、快速开始（Windows）

### 环境要求

- **Python 3.14**（`requirements.txt` 以 `==` 锁定精确版本，均为 3.14 验证通过的版本）
- 可选：Node.js（运行 JS/TS 任务）、Git for Windows（运行 `.sh` 任务，需将 `bash` 加入 PATH）

### 三步启动

```powershell
cd E:\Python_Project\panda-system

# 1. 创建虚拟环境（面板管理的 .py 任务也固定用这个 .venv 的解释器）
py -3.14 -m venv .venv

# 2. 安装依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. 启动（默认监听 0.0.0.0:3939）
.\.venv\Scripts\python.exe main.py
```

浏览器访问 <http://127.0.0.1:3939>，直接用默认账号 **`admin` / `admin`** 登录，
没有强制初始化步骤。登录后请尽快在「用户管理」中修改用户名和密码——
`admin`/`admin` 是人尽皆知的默认值，面板默认监听所有网卡，不改等于把门敞开。

换端口启动：

```powershell
$env:PD_PORT = "8000"; .\.venv\Scripts\python.exe main.py
```

### 冒烟自检（改代码后必跑）

再开一个终端，先按上面第 3 步启动服务，然后：

```powershell
$env:PYTHONIOENCODING = "utf-8"; .\.venv\Scripts\python.exe smoke_test.py
```

冒烟测试默认用 `admin`/`admin` 登录；如果你已改过密码，先设置 `$env:PD_SMOKE_PASSWORD = "新密码"` 再运行。

全部 PASS 说明登录、任务、脚本、日志、依赖、通知等链路完好。

## 五、环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PD_PORT`（或 `BACK_PORT`） | `3939` | 服务端口 |
| `PD_HOST` | `0.0.0.0` | 监听地址 |
| `PD_DIR` | 项目目录 | 安装根目录 |
| `PD_DATA_DIR` | `PD_DIR/data` | 数据目录（脚本/配置/日志/数据库） |
| `JWT_SECRET` | `panda-secret` | **生产环境必须修改**，令牌签名密钥 |
| `JWT_EXPIRES_IN` | 自动（20d/60d） | 令牌有效期，如 `20d` |
| `PD_BASE_URL` | 空 | 子路径部署前缀，如 `/panda`（需反代配合） |
| `PD_PYTHON` | 虚拟环境 python | `.py` 任务使用的解释器 |
| `PD_DATABASE_URL` | SQLite | 可换成其他 SQLAlchemy 支持的数据库 |

## 六、与青龙面板的兼容性（可选迁移）

- 密码格式与青龙一致（`scrypt$...`），旧面板账号数据可迁移；明文密码首次登录校验成功后自动升级为 scrypt。
- 环境变量保存后自动生成 `config/env.sh`、`env.js`、`env.py`（同名多值以 `&` 连接），并通过
  `PYTHONPATH` 预加载注入 Python 任务，语义与青龙一致。
- 外部状态回调接口 `PUT /api/crons/status`、仪表盘记录 `POST /api/dashboard/record` 与青龙契约相同。
- 任务命令兼容 `task / ql` 前缀、`now`、`conc ENV 1-max`、`desi ENV 1,3`、`-m 超时` 等写法。
- 任务解释器映射：`.py` → 本项目 `.venv` 的 python，`.js/.mjs/.cjs` → node，`.ts` → tsx/ts-node，`.sh` → bash，`.ps1` → powershell。

## 七、新手改代码指南

- **加一个接口**：在 `app/routers/` 对应板块文件里加函数，返回 `ok(...)/fail(...)`；需要查库就从 `app/models/` 引入表，复杂逻辑放进 `app/services/` 新函数，不要把业务写在 router 里。
- **加一张表**：`app/models/__init__.py` 定义，`core/bootstrap.py` 启动时会自动建表（SQLite）。
- **加一个配置项**：环境变量读法参考 `core/config.py`，界面上可见的设置走 `SystemRow` 表（参考 `routers/system.py`）。
- **改完就跑** `smoke_test.py`；它失败在哪一步，问题大概率就在哪个板块。

## 八、常见问题

- **`.sh` 任务不执行**：安装 Git for Windows 并将 `bash` 加入 PATH。
- **忘记登录被锁**：连续失败 3 次后按 `3^重试次数` 秒锁定，等待解锁或删除 `data/db/database.sqlite` 中 `auths` 行（谨慎）。
- **HTTPS / 反向代理**：面板只监听 HTTP，HTTPS 请在 Nginx/Caddy for Windows 层处理；反代需带
  `Upgrade`/`Connection` 头以支持 WebSocket（脚本实时输出、日志 tail 依赖它）。
- **不要多进程部署**：调度器与 WS 连接池是进程内状态，多 worker 会导致任务重复执行。
