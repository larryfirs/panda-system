"""运行记录 /api/records —— 全局任务运行实例（含历史）查询与管理。"""
import time

from fastapi import APIRouter, Body, Query

from .. import utils
from ..core import config
from ..core.common import fail, ok
from ..core.db import SessionLocal
from ..models import INSTANCE_RUNNING, Crontab, RunningInstance

router = APIRouter(prefix='/api/records', tags=['records'])

INSTANCE_STATUS = {0: 'running', 1: 'finished', 2: 'stopped', 3: 'error'}


def _dict(r: RunningInstance, cron: Crontab | None) -> dict:
    return {
        'id': r.id,
        'cron_id': r.cron_id,
        'cron_name': (cron.name or cron.command) if cron else '(任务已删除)',
        'command': cron.command if cron else '',
        'pid': r.pid,
        'log_path': r.log_path,
        'started_at': r.started_at,
        'finished_at': r.finished_at,
        'status': r.status,
        'statusText': INSTANCE_STATUS.get(r.status, 'unknown'),
        'exit_code': r.exit_code,
        'duration': (r.finished_at or int(time.time())) - (r.started_at or 0),
    }


@router.get('')
def list_records(
    page: int = Query(1),
    size: int = Query(20),
    status: int = Query(None),
    cron_id: int = Query(None),
    searchValue: str = Query(''),
):
    with SessionLocal() as s:
        q = s.query(RunningInstance, Crontab).outerjoin(
            Crontab, Crontab.id == RunningInstance.cron_id
        )
        if status is not None:
            q = q.filter(RunningInstance.status == status)
        if cron_id is not None:
            q = q.filter(RunningInstance.cron_id == cron_id)
        if searchValue:
            like = f'%{searchValue}%'
            from sqlalchemy import or_
            q = q.filter(or_(Crontab.name.like(like), Crontab.command.like(like)))
        total = q.count()
        rows = (
            q.order_by(RunningInstance.started_at.desc(), RunningInstance.id.desc())
            .offset(max(page - 1, 0) * size).limit(size).all()
        )
        return {
            'code': 200,
            'data': [_dict(r, c) for r, c in rows],
            'total': total,
        }


@router.get('/{record_id}/log')
def record_log(record_id: int, offset: int = Query(None), limit: int = Query(262144), tail: bool = Query(False)):
    with SessionLocal() as s:
        r = s.get(RunningInstance, record_id)
        if not r:
            return fail('记录不存在', 404)
        log_path, running = r.log_path, r.status == INSTANCE_RUNNING
    if not log_path or log_path == '/dev/null':
        return ok({'content': '(该任务未保留日志)' if not running else '运行中...',
                   'status': 'running' if running else 'empty',
                   'offset': 0, 'nextOffset': 0, 'total': 0, 'truncated': False})
    try:
        fp = utils.resolve_file_access(config.LOG_PATH, [log_path])
    except PermissionError:
        return fail('暂无权限', 403)
    chunk = utils.file_tail_head(fp, offset, limit, tail)
    chunk['status'] = 'running' if running else ('completed' if chunk['total'] else 'empty')
    return ok({'content': utils.remove_ansi(chunk.pop('content')), **chunk})


@router.delete('')
def delete_records(ids: list = Body(...)):
    with SessionLocal() as s:
        rows = s.query(RunningInstance).filter(RunningInstance.id.in_(ids)).all()
        for r in rows:
            if r.status == INSTANCE_RUNNING:
                return fail(f'记录 {r.id} 对应的任务仍在运行，请先停止', 405)
            s.delete(r)
        s.commit()
    return ok('删除成功')
