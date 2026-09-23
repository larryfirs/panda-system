"""日志管理 /api/logs —— 目录树 / 分片读取 / 删除 / 下载（青龙 logReader 契约）。"""
import shutil
from pathlib import Path

from fastapi import APIRouter, Body, Query
from fastapi.responses import FileResponse

from .. import utils
from ..core import config
from ..core.common import fail, ok
from ..core.db import SessionLocal
from ..models import INSTANCE_RUNNING, RunningInstance

router = APIRouter(prefix='/api/logs', tags=['log'])


def _resolve(path: str = '', filename: str = ''):
    return utils.resolve_file_access(config.LOG_PATH, [p for p in (path, filename) if p], ['.tmp'])


@router.get('')
def log_tree():
    tree = utils.walk_tree(config.LOG_PATH, '', ['.tmp', 'syslog'])
    return ok({'title': 'log', 'key': '/', 'type': 'directory', 'parent': '/', 'dir': True, 'children': tree})


@router.get('/detail')
def log_detail(
    path: str = Query(''),
    file: str = Query(''),
    offset: int = Query(None),
    limit: int = Query(262144),
    tail: bool = Query(False),
):
    try:
        fp = _resolve(path, file)
    except PermissionError:
        return fail('暂无权限', 403)
    rel = '/'.join(p for p in (path, file) if p)
    with SessionLocal() as s:
        running = (
            s.query(RunningInstance).filter(RunningInstance.log_path == rel, RunningInstance.status == INSTANCE_RUNNING).count()
        )
    chunk = utils.file_tail_head(fp, offset, limit, tail)
    data = {'code': 200, 'data': utils.remove_ansi(chunk.pop('content')), **chunk}
    if running:
        data['logStatus'] = 'running'
    return data


@router.delete('')
def delete_log(payload: dict = Body(...)):
    try:
        fp = _resolve(payload.get('path', ''), payload.get('filename') or payload.get('title') or '')
    except PermissionError:
        return fail('暂无权限', 403)
    if not fp.exists():
        return fail('文件不存在', 404)
    if fp.is_dir():
        shutil.rmtree(fp)
    else:
        fp.unlink()
    return ok('删除成功')


@router.post('/download')
def download_log(payload: dict = Body(...)):
    try:
        fp = _resolve(payload.get('path', ''), payload.get('filename') or '')
    except PermissionError:
        return fail('暂无权限', 403)
    if not fp.is_file():
        return fail('文件不存在', 404)
    return FileResponse(fp, filename=payload.get('filename') or fp.name)
