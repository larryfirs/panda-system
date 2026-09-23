"""环境变量 /api/envs —— DB 真源 + 分数式排序 + 落盘 preload 三份文件。"""
import re
from datetime import datetime

from fastapi import APIRouter, Body, File, Query, Request, UploadFile

from ..core import config
from ..services import envsync
from ..core.common import fail, ok
from ..core.db import SessionLocal
from ..models import (
    ENV_DISABLED,
    ENV_NORMAL,
    INIT_POSITION,
    MAX_POSITION,
    MIN_POSITION,
    STEP_POSITION,
    Env,
)

router = APIRouter(prefix='/api/envs', tags=['env'])

NAME_RE = re.compile(r'^[a-zA-Z_][0-9a-zA-Z_]*$')


def env_dict(e: Env) -> dict:
    return {
        'id': e.id, 'value': e.value, 'name': e.name, 'remarks': e.remarks or '',
        'status': e.status, 'position': e.position, 'isPinned': e.isPinned,
        'labels': e.labels or [], 'timestamp': e.timestamp,
        'createdAt': e.createdAt.strftime('%Y-%m-%dT%H:%M:%S.000Z') if e.createdAt else None,
        'updatedAt': e.updatedAt.strftime('%Y-%m-%dT%H:%M:%S.000Z') if e.updatedAt else None,
    }


def _resort(s):
    rows = s.query(Env).order_by(Env.isPinned.desc(), Env.position.desc(), Env.createdAt.asc()).all()
    for i, r in enumerate(rows):
        r.position = INIT_POSITION - i * STEP_POSITION
        if r.position < MIN_POSITION:
            r.position = MIN_POSITION
    s.commit()


@router.get('')
def list_envs(searchValue: str = Query('')):
    with SessionLocal() as s:
        q = s.query(Env)
        if searchValue:
            like = f'%{searchValue}%'
            from sqlalchemy import or_
            q = q.filter(or_(Env.name.like(like), Env.value.like(like), Env.remarks.like(like)))
        rows = q.order_by(Env.isPinned.desc(), Env.position.desc(), Env.createdAt.asc()).all()
        return ok([env_dict(e) for e in rows])


@router.post('')
def create_envs(payload: list = Body(...)):
    out = []
    position = None
    with SessionLocal() as s:
        created = []
        for item in payload:
            name = (item.get('name') or '').strip()
            if not NAME_RE.match(name):
                return fail(f'变量名不合法: {name}', 400)
            last = s.query(Env).order_by(Env.position.asc()).first()
            position = (last.position / 2) if last and last.position else INIT_POSITION / 2
            e = Env(
                name=name, value=item.get('value') or '',
                remarks=item.get('remarks') or '', labels=item.get('labels') or [],
                position=position, status=ENV_NORMAL,
            )
            s.add(e)
            created.append(e)
        s.commit()
        if position is not None and (position < MIN_POSITION or position > MAX_POSITION):
            _resort(s)
        envsync.set_envs(s)
        return ok([env_dict(e) for e in created])


@router.put('')
def update_env(payload: dict = Body(...)):
    if not payload.get('id'):
        return fail('缺少 id', 400)
    name = (payload.get('name') or '').strip()
    if not NAME_RE.match(name):
        return fail(f'变量名不合法: {name}', 400)
    with SessionLocal() as s:
        e = s.get(Env, payload['id'])
        if not e:
            return fail('变量不存在', 404)
        e.name = name
        e.value = payload.get('value', e.value)
        e.remarks = payload.get('remarks', e.remarks)
        e.labels = payload.get('labels', e.labels)
        e.timestamp = str(datetime.now())
        s.commit()
        envsync.set_envs(s)
        return ok(env_dict(e))


@router.delete('')
def delete_envs(ids: list = Body(...)):
    with SessionLocal() as s:
        s.query(Env).filter(Env.id.in_([int(i) for i in ids])).delete(synchronize_session=False)
        s.commit()
        envsync.set_envs(s)
    return ok('删除成功')


def _toggle(ids, status):
    with SessionLocal() as s:
        s.query(Env).filter(Env.id.in_([int(i) for i in ids])).update(
            {'status': status}, synchronize_session=False
        )
        s.commit()
        envsync.set_envs(s)


@router.put('/enable')
def enable(payload: list = Body(...)):
    _toggle(payload, ENV_NORMAL)
    return ok('启用成功')


@router.put('/disable')
def disable(payload: list = Body(...)):
    _toggle(payload, ENV_DISABLED)
    return ok('禁用成功')


@router.put('/pin')
@router.put('/unpin')
def pin(request: Request, payload: list = Body(...)):
    val = 1 if request.url.path.endswith('/pin') else 0
    with SessionLocal() as s:
        s.query(Env).filter(Env.id.in_([int(i) for i in payload])).update(
            {'isPinned': val}, synchronize_session=False
        )
        s.commit()
        envsync.set_envs(s)
    return ok('操作成功')


@router.put('/name')
def batch_remark(payload: dict = Body(...)):
    with SessionLocal() as s:
        s.query(Env).filter(Env.id.in_([int(i) for i in payload.get('ids') or []])).update(
            {'remarks': payload.get('value') or payload.get('remarks') or ''}, synchronize_session=False
        )
        s.commit()
    return ok('操作成功')


@router.put('/{env_id}/move')
def move(env_id: int, payload: dict = Body(...)):
    from_index = int(payload.get('fromIndex') or 0)
    to_index = int(payload.get('toIndex') or 0)
    with SessionLocal() as s:
        rows = s.query(Env).order_by(Env.isPinned.desc(), Env.position.desc(), Env.createdAt.asc()).all()
        if not (0 <= from_index < len(rows)) or not (0 <= to_index < len(rows)):
            return fail('索引越界', 400)
        moved = rows.pop(from_index)
        rows.insert(to_index, moved)
        anchor_before = rows[to_index - 1].position if to_index > 0 else None
        anchor_after = rows[to_index + 1].position if to_index + 1 < len(rows) else None
        if anchor_before is not None and anchor_after is not None:
            moved.position = (anchor_before + anchor_after) / 2
        elif anchor_before is not None:
            moved.position = anchor_before / 2
        elif anchor_after is not None:
            moved.position = anchor_after * 2
        if moved.position < MIN_POSITION or moved.position > MAX_POSITION:
            _resort(s)
        s.commit()
        envsync.set_envs(s)
    return ok('移动成功')


@router.post('/labels')
@router.delete('/labels')
def labels(request: Request, payload: dict = Body(...)):
    ids = [int(i) for i in payload.get('ids') or []]
    tags = payload.get('labels') or []
    removing = request.method == 'DELETE'
    with SessionLocal() as s:
        for e in s.query(Env).filter(Env.id.in_(ids)).all():
            cur = list(e.labels or [])
            cur = [x for x in cur if x not in tags] if removing else cur + [x for x in tags if x not in cur]
            e.labels = cur
        s.commit()
    return ok('操作成功')


@router.post('/upload')
async def upload_env(file: UploadFile = File(...)):
    import json
    try:
        data = json.loads((await file.read()).decode('utf-8'))
    except Exception:
        return fail('文件格式错误，应为导出的 JSON', 400)
    payload = data.get('data') if isinstance(data, dict) else data
    if not isinstance(payload, list):
        return fail('文件格式错误', 400)
    return create_envs(payload)


# 放在最后避免吞掉静态路径
@router.get('/{env_id}')
def get_env(env_id: int):
    with SessionLocal() as s:
        e = s.get(Env, env_id)
        return ok(env_dict(e)) if e else fail('变量不存在', 404)
