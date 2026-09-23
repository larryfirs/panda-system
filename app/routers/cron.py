"""定时任务 /api/crons —— 对齐青龙 services/cron.ts 行为。"""
import json
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Body, Query, Request

from .. import utils
from ..core import config
from ..services import auth, executor
from ..core.common import fail, ok
from ..core.db import SessionLocal
from ..models import (
    CRON_DISABLED,
    CRON_IDLE,
    CRON_QUEUED,
    CRON_RUNNING,
    INIT_POSITION,
    STEP_POSITION,
    VIEW_PERSONAL,
    VIEW_SYSTEM,
    Crontab,
    CronView,
    RunningInstance,
)
from ..services.scheduler import ONCE, BOOT, is_special_schedule, panda_scheduler, validate_schedule

router = APIRouter(prefix='/api/crons', tags=['cron'])


def render_crontab_list():
    """把 DB 任务镜像写入 data/config/crontab.list（青龙 autosave_crontab）。"""
    with SessionLocal() as s:
        crons = s.query(Crontab).all()
        lines = ['\n# 熊猫系统任务列表（自动生成，请勿手工编辑）\n']
        for c in crons:
            commented = (
                not c.is_active()
                or is_special_schedule(c.schedule)
                or len((c.schedule or '').split()) > 5
            )
            line = f'{c.schedule} task {c.command}'
            lines.append(('# ' if commented else '') + line + '\n')
        config.CRONTAB_FILE.write_text(''.join(lines), 'utf-8')


def sync_scheduler(cron):
    try:
        if cron.is_active():
            panda_scheduler.sync_cron(cron)
        else:
            panda_scheduler.remove_cron(cron.id)
    except Exception as e:
        print('[scheduler] 注册失败:', e)


def cron_dict(c: Crontab) -> dict:
    d = {
        'id': c.id, 'name': c.name, 'command': c.command, 'schedule': c.schedule,
        'timestamp': c.timestamp, 'saved': c.saved, 'status': c.status,
        'isSystem': c.isSystem, 'pid': c.pid, 'isDisabled': c.isDisabled,
        'isPinned': c.isPinned, 'log_path': c.log_path, 'labels': c.labels or [],
        'last_running_time': c.last_running_time, 'last_execution_time': c.last_execution_time,
        'sub_id': c.sub_id, 'extra_schedules': c.extra_schedules,
        'task_before': c.task_before, 'task_after': c.task_after,
        'log_name': c.log_name, 'allow_multiple_instances': c.allow_multiple_instances,
        'work_dir': c.work_dir,
        'createdAt': c.createdAt.strftime('%Y-%m-%dT%H:%M:%S.000Z') if c.createdAt else None,
        'updatedAt': c.updatedAt.strftime('%Y-%m-%dT%H:%M:%S.000Z') if c.updatedAt else None,
    }
    return d


OPERATIONS = ('Reg', 'NotReg', 'In', 'Nin')


def _apply_filters(query, filters, relation='and'):
    from sqlalchemy import and_, or_
    conds = []
    for f in filters or []:
        prop = f.get('property')
        op = f.get('operation')
        value = f.get('value')
        col = getattr(Crontab, prop, None)
        if col is None or prop in ('createdAt', 'updatedAt'):
            continue
        if prop == 'status':
            try:
                sv = int(value)
            except (TypeError, ValueError):
                continue
            if sv == CRON_DISABLED:
                conds.append(or_(Crontab.status == sv, Crontab.isDisabled == 1))
            else:
                conds.append(and_(Crontab.status == sv, Crontab.isDisabled == 0))
            continue
        if op == 'Reg':
            conds.append(col.like(f'%{value}%'))
        elif op == 'NotReg':
            conds.append(or_(~col.like(f'%{value}%'), col.is_(None)))
        elif op == 'In':
            vals = value if isinstance(value, list) else [value]
            conds.append(col.in_(vals))
        elif op == 'Nin':
            vals = value if isinstance(value, list) else [value]
            conds.append(or_(~col.in_(vals), col.is_(None)))
    if not conds:
        return query
    return query.filter(or_(*conds) if relation == 'or' else and_(*conds))


def _parse_json_param(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


@router.get('')
def list_crons(
    searchValue: str = Query(''),
    page: int = Query(1),
    size: int = Query(0),
    sorter: str = Query(''),
    filters: str = Query(''),
    queryString: str = Query(''),
):
    qs = _parse_json_param(queryString) or {}
    eff_filters = _parse_json_param(filters) or qs.get('filters')
    eff_sorter = _parse_json_param(sorter) or qs.get('sorts')
    relation = qs.get('filterRelation', 'and')
    with SessionLocal() as s:
        query = s.query(Crontab)
        if searchValue:
            like = f'%{searchValue}%'
            from sqlalchemy import or_
            query = query.filter(or_(Crontab.name.like(like), Crontab.command.like(like)))
        query = _apply_filters(query, eff_filters, relation)

        order = [Crontab.isPinned.desc(), Crontab.id.desc()]
        if isinstance(eff_sorter, list) and eff_sorter:
            order = []
            for srt in eff_sorter:
                col = getattr(Crontab, srt.get('value') or '', None)
                if col is None:
                    continue
                order.append(col.asc() if srt.get('type') == 'ASC' else col.desc())
            order.append(Crontab.isPinned.desc())
        query = query.order_by(*order)

        total = query.count()
        if size:
            rows = query.offset(max(page - 1, 0) * size).limit(size).all()
        else:
            rows = query.all()
        return {'code': 200, 'data': [cron_dict(c) for c in rows], 'total': total}


@router.get('/detail')
def detail_by_log(log_path: str = Query('')):
    with SessionLocal() as s:
        rows = s.query(Crontab).filter(Crontab.log_path == log_path).all()
        return ok([cron_dict(c) for c in rows])


def _common_fields(payload: dict) -> dict:
    fields = {}
    for key in ('name', 'command', 'schedule', 'labels', 'sub_id', 'extra_schedules',
                'task_before', 'task_after', 'log_name', 'allow_multiple_instances', 'work_dir'):
        if key in payload:
            fields[key] = payload[key]
    return fields


def _validate(fields: dict):
    command = (fields.get('command') or '').strip()
    if not command:
        return '命令不能为空'
    if not fields.get('name'):
        fields['name'] = command[:20]
    fields['command'] = command
    err = validate_schedule(fields.get('schedule') or '')
    if err:
        return err
    log_name = (fields.get('log_name') or '').strip()
    if log_name and log_name != '/dev/null':
        if len(log_name) > 100 or '\\' in log_name or log_name.startswith('/'):
            return 'log_name 不合法'
        fields['log_name'] = log_name
    for e in fields.get('extra_schedules') or []:
        err = validate_schedule((e or {}).get('schedule') or '')
        if err:
            return err
    return None


@router.post('')
def create_cron(payload: dict = Body(...)):
    fields = _common_fields(payload)
    err = _validate(fields)
    if err:
        return fail(err, 400)
    with SessionLocal() as s:
        c = Crontab(**fields, status=CRON_IDLE, saved=1)
        s.add(c)
        s.commit()
        s.refresh(c)
        sync_scheduler(c)
        render_crontab_list()
        return ok(cron_dict(c))


@router.put('')
def update_cron(payload: dict = Body(...)):
    cid = payload.get('id')
    if not cid:
        return fail('缺少 id', 400)
    fields = _common_fields(payload)
    err = _validate(fields)
    if err:
        return fail(err, 400)
    with SessionLocal() as s:
        c = s.get(Crontab, cid)
        if not c:
            return fail('任务不存在', 404)
        if c.status == CRON_RUNNING:
            return fail('任务运行中，无法编辑', 405)
        for k, v in fields.items():
            setattr(c, k, v)
        c.timestamp = str(datetime.now())
        c.saved = 1
        s.commit()
        sync_scheduler(c)
        render_crontab_list()
        return ok(cron_dict(c))


@router.delete('')
def delete_crons(ids: list = Body(...)):
    with SessionLocal() as s:
        for cid in ids:
            c = s.get(Crontab, cid)
            if not c:
                continue
            if c.status == CRON_RUNNING:
                executor.stop_cron(cid)
            panda_scheduler.remove_cron(cid)
            s.delete(c)
        s.commit()
    render_crontab_list()
    return ok('删除成功')


@router.put('/run')
def run_crons(ids: list = Body(...)):
    with SessionLocal() as s:
        for cid in ids:
            c = s.get(Crontab, cid)
            if not c:
                continue
            token = str(uuid.uuid4())
            s.query(Crontab).filter(Crontab.id == cid).update({'status': CRON_QUEUED, 'queued_token': token})
            s.commit()
            executor.manual_run(cid, token)
    return ok('请求成功')


@router.put('/stop')
def stop_crons(ids: list = Body(...)):
    for cid in ids:
        executor.stop_cron(cid)
    return ok('停止成功')


@router.put('/enable')
def enable_crons(ids: list = Body(...)):
    with SessionLocal() as s:
        crons = s.query(Crontab).filter(Crontab.id.in_(ids)).all()
        for c in crons:
            c.isDisabled = 0
            c.status = CRON_IDLE
        s.commit()
        for c in crons:
            sync_scheduler(c)
    render_crontab_list()
    return ok('启用成功')


@router.put('/disable')
def disable_crons(ids: list = Body(...)):
    with SessionLocal() as s:
        crons = s.query(Crontab).filter(Crontab.id.in_(ids)).all()
        for c in crons:
            c.isDisabled = 1
            c.status = CRON_DISABLED
        s.commit()
        for c in crons:
            if c.status == CRON_RUNNING:
                executor.stop_cron(c.id)
            panda_scheduler.remove_cron(c.id)
    render_crontab_list()
    return ok('禁用成功')


@router.put('/pin')
@router.put('/unpin')
def pin_crons(request: Request, ids: list = Body(...)):
    val = 1 if request.url.path.endswith('/pin') else 0
    with SessionLocal() as s:
        s.query(Crontab).filter(Crontab.id.in_(ids)).update({'isPinned': val}, synchronize_session=False)
        s.commit()
    return ok('操作成功')


@router.put('/status')
def update_status(payload: dict = Body(...)):
    """外部任务进程状态回调（青龙 PUT /api/crons/status 契约保持）。"""
    ids = payload.get('ids') or []
    status = int(payload.get('status', CRON_IDLE))
    log_path = payload.get('log_path')
    with SessionLocal() as s:
        for cid in ids:
            c = s.get(Crontab, int(cid))
            if not c:
                continue
            upd = {}
            if status == CRON_RUNNING:
                upd = {
                    'status': CRON_RUNNING,
                    'pid': payload.get('pid'),
                    'log_path': log_path or c.log_path,
                    'last_execution_time': int(payload.get('last_execution_time') or time.time()),
                }
                inst = (
                    s.query(RunningInstance)
                    .filter(
                        RunningInstance.cron_id == c.id,
                        RunningInstance.status == 0,
                        RunningInstance.log_path == (log_path or c.log_path),
                    )
                    .first()
                )
                if not inst:
                    s.add(RunningInstance(
                        cron_id=c.id, pid=payload.get('pid'), log_path=log_path or '',
                        started_at=int(payload.get('last_execution_time') or time.time()),
                    ))
            elif status == CRON_IDLE:
                if log_path and c.log_path and log_path != c.log_path:
                    continue  # 青龙：日志路径不同则不覆盖
                upd = {'status': CRON_IDLE, 'pid': None}
                if payload.get('last_running_time') is not None:
                    upd['last_running_time'] = int(payload['last_running_time'])
                exit_code = payload.get('exit_code')
                if exit_code is not None:
                    inst = (
                        s.query(RunningInstance)
                        .filter(RunningInstance.cron_id == c.id, RunningInstance.status == 0)
                        .order_by(RunningInstance.id.desc())
                        .first()
                    )
                    if inst:
                        inst.status = 1 if int(exit_code) == 0 else 3
                        inst.finished_at = int(time.time())
                        inst.exit_code = int(exit_code)
            for k, v in upd.items():
                if v is not None or k in ('pid',):
                    setattr(c, k, v)
        s.commit()
    return ok('更新成功')


@router.post('/labels')
@router.delete('/labels')
def add_labels(request: Request, payload: dict = Body(...)):
    ids = payload.get('ids') or []
    labels = payload.get('labels') or []
    removing = request.method == 'DELETE'
    with SessionLocal() as s:
        for cid in ids:
            c = s.get(Crontab, cid)
            if not c:
                continue
            cur = list(c.labels or [])
            if removing:
                cur = [x for x in cur if x not in labels]
            else:
                cur += [x for x in labels if x not in cur]
            c.labels = cur
        s.commit()
    return ok('操作成功')


# ---------- 任务日志（分片读取，青龙 offset/nextOffset 契约） ----------

@router.get('/{cron_id}/log')
def cron_log(cron_id: int, offset: int = Query(None), limit: int = Query(262144), tail: bool = Query(False)):
    with SessionLocal() as s:
        c = s.get(Crontab, cron_id)
        if not c:
            return fail('任务不存在', 404)
        if c.log_name == '/dev/null':
            return ok({'content': '', 'status': 'ignored', 'offset': 0, 'nextOffset': 0, 'total': 0, 'truncated': False})
        log_path = c.log_path or ''
        running = (
            s.query(RunningInstance)
            .filter(RunningInstance.cron_id == cron_id, RunningInstance.status == 0)
            .count()
        )
    if not log_path:
        return ok({'content': '', 'status': 'notFound', 'offset': 0, 'nextOffset': 0, 'total': 0, 'truncated': False})
    try:
        fp = utils.resolve_file_access(config.LOG_PATH, [log_path])
    except PermissionError:
        return fail('暂无权限', 403)
    chunk = utils.file_tail_head(fp, offset, limit, tail)
    if running:
        chunk['status'] = 'running'
        if not chunk['content']:
            chunk['content'] = '运行中...'
    else:
        chunk['status'] = 'completed' if chunk['total'] else 'empty'
    return ok({'content': utils.remove_ansi(chunk.pop('content')), **chunk})


@router.get('/{cron_id}/logs')
def cron_logs(cron_id: int):
    with SessionLocal() as s:
        c = s.get(Crontab, cron_id)
        uniq = executor.get_uniq_log_dir(s, c) if c else None
    if not uniq:
        return ok([])
    directory = config.LOG_PATH / uniq
    files = []
    if directory.is_dir():
        for e in directory.iterdir():
            if e.suffix == '.log' and e.is_file():
                files.append({
                    'filename': e.name,
                    'directory': uniq,
                    'time': e.stat().st_mtime * 1000,
                })
    files.sort(key=lambda x: -x['time'])
    return ok(files)


@router.get('/{cron_id}/instances')
def cron_instances(cron_id: int):
    with SessionLocal() as s:
        rows = (
            s.query(RunningInstance)
            .filter(RunningInstance.cron_id == cron_id)
            .order_by(RunningInstance.started_at.desc())
            .all()
        )
        return ok([{
            'id': r.id, 'cron_id': r.cron_id, 'pid': r.pid, 'log_path': r.log_path,
            'started_at': r.started_at, 'finished_at': r.finished_at,
            'status': r.status, 'exit_code': r.exit_code,
        } for r in rows])


@router.post('/{cron_id}/instances/{instance_id}/stop')
def stop_instance(cron_id: int, instance_id: int):
    with SessionLocal() as s:
        inst = s.get(RunningInstance, instance_id)
        if not inst or inst.status != 0:
            return fail('实例不存在或已结束', 404)
        if inst.pid:
            utils.kill_process_tree(inst.pid)
        inst.status = 2
        inst.finished_at = int(time.time())
        inst.exit_code = 143
        s.commit()
    return ok('已停止')


@router.get('/import')
def import_system_crontab():
    """导入系统 crontab（Windows 或未装 crontab 时返回空）。"""
    import shutil
    import subprocess
    if not shutil.which('crontab'):
        return ok([])
    try:
        out = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ok([])
    items = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(None, 5)
        if len(parts) >= 6:
            items.append({'schedule': ' '.join(parts[:5]), 'command': parts[5]})
    return ok(items)


# ---------- 视图（分组） ----------

def view_dict(v: CronView) -> dict:
    return {
        'id': v.id, 'name': v.name, 'position': v.position, 'isDisabled': v.isDisabled,
        'filters': v.filters or [], 'sorts': v.sorts or [],
        'filterRelation': v.filterRelation, 'type': v.type,
    }


@router.get('/views')
def list_views():
    with SessionLocal() as s:
        rows = s.query(CronView).order_by(CronView.position.desc()).all()
        return ok([view_dict(v) for v in rows])


@router.post('/views')
def create_view(payload: dict = Body(...)):
    name = (payload.get('name') or '').strip()
    if not name:
        return fail('视图名称不能为空', 400)
    with SessionLocal() as s:
        if s.query(CronView).filter(CronView.name == name).first():
            return fail('视图名称已存在', 400)
        last = s.query(CronView).order_by(CronView.position.asc()).first()
        position = (last.position / 2) if last and last.position else INIT_POSITION / 2
        v = CronView(
            name=name, position=position,
            filters=payload.get('filters') or [], sorts=payload.get('sorts') or [],
            filterRelation=payload.get('filterRelation'), type=VIEW_PERSONAL,
        )
        s.add(v)
        s.commit()
        return ok(view_dict(v))


@router.put('/views')
def update_view(payload: dict = Body(...)):
    with SessionLocal() as s:
        v = s.get(CronView, payload.get('id'))
        if not v:
            return fail('视图不存在', 404)
        if v.type == VIEW_SYSTEM and (payload.get('name') or v.name) != v.name:
            return fail('系统视图不可重命名', 403)
        v.name = (payload.get('name') or v.name).strip()
        v.filters = payload.get('filters', v.filters) or []
        v.sorts = payload.get('sorts', v.sorts) or []
        v.filterRelation = payload.get('filterRelation', v.filterRelation)
        s.commit()
        return ok(view_dict(v))


@router.delete('/views')
def delete_views(ids: list = Body(...)):
    with SessionLocal() as s:
        rows = s.query(CronView).filter(CronView.id.in_(ids), CronView.type != VIEW_SYSTEM).all()
        for r in rows:
            s.delete(r)
        s.commit()
    return ok('删除成功')


@router.put('/views/move')
def move_view(payload: dict = Body(...)):
    from_id, to_id = payload.get('from'), payload.get('to')
    with SessionLocal() as s:
        rows = s.query(CronView).order_by(CronView.position.desc()).all()
        by_id = {r.id: r for r in rows}
        src = by_id.get(from_id)
        dst = by_id.get(to_id)
        if not src or not dst:
            return fail('视图不存在', 404)
        direction = payload.get('direction')
        positions = [r.position for r in rows]
        if direction in ('top',):
            src.position = positions[0] * 2 if positions[0] > 0 else INIT_POSITION * 2
        elif direction in ('bottom',):
            src.position = positions[-1] / 2 if positions else INIT_POSITION / 2
        else:
            idx = positions.index(dst.position)
            if direction == 'up':
                above = positions[idx - 1] if idx > 0 else dst.position + STEP_POSITION
                src.position = (above + dst.position) / 2
            else:
                below = positions[idx + 1] if idx + 1 < len(positions) else dst.position / 2
                src.position = (below + dst.position) / 2
        s.commit()
    return ok('移动成功')


@router.put('/views/enable')
@router.put('/views/disable')
def toggle_views(request: Request, payload: dict = Body(...)):
    val = 0 if request.url.path.endswith('/enable') else 1
    with SessionLocal() as s:
        ids = payload.get('ids') if isinstance(payload, dict) else payload
        s.query(CronView).filter(CronView.id.in_(ids or [])).update(
            {'isDisabled': val}, synchronize_session=False
        )
        s.commit()
    return ok('操作成功')


# 放在最后：避免吞掉 /views、/detail、/import 等静态路径
@router.get('/{cron_id}')
def get_cron(cron_id: int):
    with SessionLocal() as s:
        c = s.get(Crontab, cron_id)
        if not c:
            return fail('任务不存在', 404)
        return ok(cron_dict(c))
