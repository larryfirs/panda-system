import os
import sys
from pathlib import Path

# 安装根目录（对应青龙的 QL_DIR），默认项目根目录
PD_DIR = Path(os.environ.get('PD_DIR') or Path(__file__).resolve().parent.parent.parent).resolve()
# 数据目录（对应 QL_DATA_DIR）
DATA_DIR = Path(os.environ.get('PD_DATA_DIR') or PD_DIR / 'data').resolve()

SCRIPT_PATH = DATA_DIR / 'scripts'
CONFIG_PATH = DATA_DIR / 'config'
LOG_PATH = DATA_DIR / 'log'
DB_DIR = DATA_DIR / 'db'
BAK_PATH = DATA_DIR / 'bak'
UPLOAD_PATH = DATA_DIR / 'upload'
REPO_PATH = DATA_DIR / 'repo'
SYSLOG_PATH = DATA_DIR / 'syslog'
TMP_LOG_PATH = LOG_PATH / '.tmp'
PRELOAD_PATH = CONFIG_PATH / 'preload'

ENV_SH_FILE = CONFIG_PATH / 'env.sh'
ENV_JS_FILE = CONFIG_PATH / 'env.js'
ENV_PY_FILE = CONFIG_PATH / 'env.py'
CRONTAB_FILE = CONFIG_PATH / 'crontab.list'
TOKEN_FILE = CONFIG_PATH / 'token.json'

STATIC_DIR = PD_DIR / 'static'
SAMPLES_DIR = PD_DIR / 'samples'

DATABASE_URL = os.environ.get(
    'PD_DATABASE_URL', 'sqlite:///' + (DB_DIR / 'database.sqlite').as_posix()
)

HOST = os.environ.get('PD_HOST', '0.0.0.0')
PORT = int(os.environ.get('PD_PORT') or os.environ.get('BACK_PORT') or 3939)
# 子路径部署（对应 QlBaseUrl），如 '/panda'
BASE_URL = ('/' + os.environ.get('PD_BASE_URL', '').strip('/')).rstrip('/')
if BASE_URL == '/':
    BASE_URL = ''

JWT_SECRET = os.environ.get('JWT_SECRET') or 'panda-secret'
JWT_ALGORITHM = 'HS384'
JWT_EXPIRES_IN = os.environ.get('JWT_EXPIRES_IN')  # 如 '20d' / '60d'，留空自动

# 任务脚本使用的 Python 解释器：默认使用本项目的虚拟环境
PYTHON_BIN = os.environ.get('PD_PYTHON') or sys.executable
# 系统 crontab 兼容命令前缀
TASK_COMMAND = 'task'
QL_COMMAND = 'ql'

# 禁止通过配置/脚本接口读写的文件
BLACK_FILE_LIST = [
    'auth.json',
    'token.json',
    'env.sh',
    'env.js',
    'env.py',
    'crontab.list',
    'config.sh.sample',
    'cookie.sh',
    'dependence-proxy.sh',
    '__pycache__',
]
# 脚本文件树中隐藏的目录/文件
BLACK_DIR_LIST = ['node_modules', '.git', '.pnpm']

MAX_TOKENS_PER_PLATFORM = 10
DEFAULT_TIMEZONE = 'Asia/Shanghai'
DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = 'admin'

# 免鉴权路径（相对 BASE_URL）
API_WHITE_LIST = [
    '/api/user/login',
    '/api/user/two-factor/login',
    '/api/user/init',
    '/api/user/notification/init',
    '/api/system',
    '/api/health',
    '/api/env.js',
    '/api/env.py',
    '/api/env.sh',
    '/api/static',
]

IS_WINDOWS = sys.platform == 'win32'


def jwt_expires_seconds(two_factor_activated: bool) -> int:
    if JWT_EXPIRES_IN:
        raw = JWT_EXPIRES_IN.lower().rstrip('d')
        try:
            return int(float(raw) * 86400)
        except ValueError:
            pass
    return 60 * 86400 if two_factor_activated else 20 * 86400


ALL_DIRS = [
    DATA_DIR,
    SCRIPT_PATH,
    CONFIG_PATH,
    LOG_PATH,
    DB_DIR,
    BAK_PATH,
    UPLOAD_PATH,
    REPO_PATH,
    SYSLOG_PATH,
    PRELOAD_PATH,
]


def ensure_dirs():
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)
