"""配置文件 /api/configs —— 对比工具的后端数据源（样本 vs 当前文件）。

样本映射与青龙 SAMPLE_FILES 对应：
  sample/config.sample.sh → data/config/config.sh
  sample/notify.js        → data/scripts/sendNotify.js
  sample/notify.py        → data/scripts/notify.py
"""
from pathlib import Path

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import PlainTextResponse

from ..core import config
from ..core.common import fail, ok

router = APIRouter(prefix='/api/configs', tags=['config'])

SAMPLE_FILES = [
    {'origin': 'sample/config.sample.sh', 'extra': {'target': 'config.sh'}},
    {'origin': 'sample/notify.js', 'extra': {'target': 'sendNotify.js'}},
    {'origin': 'sample/notify.py', 'extra': {'target': 'notify.py'}},
]


def _read_sample(origin: str):
    fp = config.SAMPLES_DIR / Path(origin).name
    if fp.exists():
        return fp.read_text('utf-8', errors='replace')
    origin_qinglong = config.PD_DIR.parent / origin  # 兼容读取原青龙仓库样本
    if origin_qinglong.exists():
        return origin_qinglong.read_text('utf-8', errors='replace')
    return ''


@router.get('/samples')
def samples():
    return ok(SAMPLE_FILES)


@router.get('/files')
def files():
    names = [
        p.name for p in config.CONFIG_PATH.iterdir()
        if p.is_file() and p.name not in config.BLACK_FILE_LIST
    ]
    return ok(names)


SCRIPT_TARGETS = {'sendNotify.js', 'notify.py'}


def _resolve_rel(path: str):
    """归一化相对路径 → (根目录, 相对路径)。

    兼容三种写法：'config/config.sh'、'scripts/notify.py'、裸文件名。
    sendNotify.js / notify.py 属于脚本目录（与青龙一致）。
    """
    p = str(path).replace('\\', '/').lstrip('/')
    if p.startswith('config/'):
        p = p[len('config/'):]
    if p.startswith('scripts/'):
        return config.SCRIPT_PATH, p[len('scripts/'):]
    if p in SCRIPT_TARGETS:
        return config.SCRIPT_PATH, p
    return config.CONFIG_PATH, p


@router.get('/detail')
def detail(path: str = Query(...)):
    p = Path(path.replace('\\', '/'))
    if p.parts and p.parts[0] == 'sample':
        return ok(_read_sample(path))
    from .. import utils
    root, rel = _resolve_rel(path)
    try:
        fp = utils.resolve_file_access(root, [rel], config.BLACK_FILE_LIST)
    except PermissionError:
        return fail('暂无权限', 403)
    if not fp.is_file():
        return fail('文件不存在', 404)
    return ok(fp.read_text('utf-8', errors='replace'))


@router.post('/save')
def save(payload: dict = Body(...)):
    from .. import utils
    name = payload.get('name') or ''
    content = payload.get('content') or ''
    root, rel = _resolve_rel(name)
    try:
        fp = utils.resolve_file_access(root, [rel], config.BLACK_FILE_LIST)
    except PermissionError:
        return fail('暂无权限', 403)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, 'utf-8')
    return ok('保存成功')
