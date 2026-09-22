"""依赖管理 /api/dependencies —— 串行安装队列（青龙 dependenyLimit 并发 1）。

类型: 0=nodejs(npm/pnpm 全局) 1=python3(安装进熊猫系统虚拟环境) 2=linux(apk/apt)
日志逐段落库 + WebSocket 推送（installDependence/uninstallDependence）。
"""
import importlib.metadata
import subprocess
import threading
import time
from queue import Queue

from fastapi import APIRouter, Body, Query

from .. import config, utils
from ..common import fail, ok
from ..db import SessionLocal
from ..models import (
    DEP_CANCELLED,
    DEP_INSTALLED,
    DEP_INSTALL_FAILED,
    DEP_INSTALLING,
    DEP_LINUX,
    DEP_NODEJS,
    DEP_PYTHON3,
    DEP_QUEUED,
    DEP_REMOVED,
    DEP_REMOVING,
    DEP_REMOVE_FAILED,
    DEP_TYPE_NAMES,
    Dependence,
)
from ..ws import ws_manager

router = APIRouter(prefix='/api/dependencies', tags=['dependence'])

_queue: Queue = Queue()
_worker_started = False
_current = {}  # dep_id -> Popen


def dep_dict(d: Dependence) -> dict:
    return {
        'id': d.id, 'name': d.name, 'type': d.type, 'status': d.status,
        'log': d.log or [], 'remark': d.remark or '', 'timestamp': d.timestamp,
        'createdAt': d.createdAt.strftime('%Y-%m-%dT%H:%M:%S.000Z') if d.createdAt else None,
        'updatedAt': d.updatedAt.strftime('%Y-%m-%dT%H:%M:%S.000Z') if d.updatedAt else None,
    }


# ---------- 命令映射 ----------

def _install_cmds(dep_type: int, name: str):
    if dep_type == DEP_PYTHON3:
        return (
            [config.PYTHON_BIN, '-m', 'pip', 'install', '--disable-pip-version-check',
             '--root-user-action=ignore', name],
            [config.PYTHON_BIN, '-m', 'pip', 'uninstall', '-y', name],
        )
    if dep_type == DEP_NODEJS:
        pm = utils.which_or_none('pnpm') or utils.which_or_none('npm')
        if not pm:
            raise FileNotFoundError('未找到 npm/pnpm，无法安装 Node 依赖')
        if pm.endswith('pnpm'):
            return [pm, 'add', '-g', name], [pm, 'remove', '-g', name]
        return [pm, 'install', '-g', name], [pm, 'uninstall', '-g', name]
    if dep_type == DEP_LINUX:
        apt = utils.which_or_none('apt-get')
        apk = utils.which_or_none('apk')
        if apt:
            return [apt, 'install', '-y', name], [apt, 'remove', '-y', name]
        if apk:
            return [apk, 'add', '--no-check-certificate', name], [apk, 'del', name]
        raise FileNotFoundError('未找到 apt-get/apk，Linux 系统依赖仅支持 Debian/Ubuntu/Alpine')
    raise ValueError(f'未知依赖类型: {dep_type}')


def _probe(dep_type: int, name: str) -> bool:
    """探测依赖是否已安装。"""
    pkg = _strip_version(name)
    try:
        if dep_type == DEP_PYTHON3:
            try:
                importlib.metadata.version(pkg)
                return True
            except importlib.metadata.PackageNotFoundError:
                return False
        if dep_type == DEP_NODEJS:
            pm = utils.which_or_none('pnpm') or utils.which_or_none('npm')
            if not pm:
                return False
            out = subprocess.run(
                [pm, 'ls', '-g'] + (['--depth=0'] if 'npm' in pm else []),
                capture_output=True, text=True, timeout=60,
            ).stdout or ''
            return any(line.strip().split()[0] == pkg for line in out.splitlines() if line.strip())
        if dep_type == DEP_LINUX:
            dpkg = utils.which_or_none('dpkg-query')
            if dpkg:
                r = subprocess.run([dpkg, '-s', pkg], capture_output=True, text=True, timeout=30)
                return 'install ok installed' in (r.stdout or '')
            apk = utils.which_or_none('apk')
            if apk:
                r = subprocess.run([apk, 'info', '-es', pkg], capture_output=True, timeout=30)
                return r.returncode == 0
    except Exception:
        return False
    return False


import re

VER_RE = re.compile(r'^(@?[^@=]+)(?:@|==|=)(.+)$')


def _strip_version(name: str) -> str:
    name = (name or '').strip()
    m = VER_RE.match(name)
    return m.group(1) if m else name


# ---------- 安装队列 ----------

def start_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_worker_loop, daemon=True, name='dependence-worker').start()


def enqueue(dep_id: int):
    start_worker()
    _queue.put(dep_id)


def _worker_loop():
    while True:
        dep_id = _queue.get()
        try:
            _process(dep_id)
        except Exception as e:
            _append_log(dep_id, f'[错误] {e}')
            _set_status(dep_id, DEP_INSTALL_FAILED)
        finally:
            _queue.task_done()


def _append_log(dep_id: int, line: str):
    with SessionLocal() as s:
        d = s.get(Dependence, dep_id)
        if not d:
            return
        log = list(d.log or [])
        for part in (line or '').splitlines() or ['']:
            log.append(part)
        d.log = log[-500:]
        s.commit()
        msg_type = 'uninstallDependence' if d.status in (DEP_REMOVING, DEP_REMOVE_FAILED, DEP_REMOVED) else 'installDependence'
    ws_manager.broadcast(msg_type, line, references=[dep_id])


def _set_status(dep_id: int, status: int):
    with SessionLocal() as s:
        d = s.get(Dependence, dep_id)
        if d:
            d.status = status
            s.commit()


def _process(dep_id: int):
    with SessionLocal() as s:
        d = s.get(Dependence, dep_id)
        if not d or d.status == DEP_CANCELLED:
            return
        removing = d.status == DEP_REMOVING
        name, dep_type = d.name, d.type
    try:
        install_cmd, remove_cmd = _install_cmds(dep_type, name)
    except Exception as e:
        _append_log(dep_id, f'[错误] {e}')
        _set_status(dep_id, DEP_REMOVE_FAILED if removing else DEP_INSTALL_FAILED)
        return

    if not removing:
        if _probe(dep_type, name):
            _append_log(dep_id, '依赖已安装，跳过安装')
            _set_status(dep_id, DEP_INSTALLED)
            return
        _set_status(dep_id, DEP_INSTALLING)
        cmd = install_cmd
    else:
        _set_status(dep_id, DEP_REMOVING)
        cmd = remove_cmd

    _append_log(dep_id, f'$ {" ".join(cmd)}')
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(config.DATA_DIR), env={**dict(__import__('os').environ)},
        )
        _current[dep_id] = proc
        for line in iter(proc.stdout.readline, b''):
            text = line.decode('utf-8', 'replace').rstrip('\r\n')
            if text:
                _append_log(dep_id, text)
        code = proc.wait()
    except Exception as e:
        _append_log(dep_id, f'[错误] {e}')
        code = 1
    finally:
        _current.pop(dep_id, None)

    with SessionLocal() as s:
        d = s.get(Dependence, dep_id)
        if not d or d.status == DEP_CANCELLED:
            return
    if removing:
        if code == 0:
            _append_log(dep_id, '卸载成功')
            with SessionLocal() as s:
                s.query(Dependence).filter(Dependence.id == dep_id).delete()
                s.commit()
        else:
            _append_log(dep_id, '卸载失败')
            _set_status(dep_id, DEP_REMOVE_FAILED)
    else:
        if code == 0:
            _append_log(dep_id, '安装成功')
            _set_status(dep_id, DEP_INSTALLED)
        else:
            _append_log(dep_id, '安装失败')
            _set_status(dep_id, DEP_INSTALL_FAILED)


# ---------- 路由 ----------

@router.get('')
def list_deps(searchValue: str = Query(''), type: str = Query(''), status: str = Query('')):
    with SessionLocal() as s:
        q = s.query(Dependence)
        if searchValue:
            q = q.filter(Dependence.name.like(f'%{searchValue}%'))
        if type:
            names = {v: k for k, v in DEP_TYPE_NAMES.items()}
            for t in type.split(','):
                if t in names:
                    q = q.filter(Dependence.type == names[t])
        if status:
            q = q.filter(Dependence.status.in_([int(x) for x in status.split(',') if x.isdigit()]))
        rows = q.order_by(Dependence.id.desc()).all()
        # installed 状态真实探测回写（青龙 getDependenceCommand）
        changed = False
        for d in rows:
            if d.status == DEP_INSTALLED and not _probe(d.type, d.name):
                d.status = DEP_INSTALL_FAILED
                changed = True
        if changed:
            s.commit()
        return ok([dep_dict(d) for d in rows])


@router.post('')
def create_deps(payload: list = Body(...)):
    out = []
    with SessionLocal() as s:
        for item in payload:
            name = (item.get('name') or '').strip()
            if not name:
                return fail('依赖名称不能为空', 400)
            d = Dependence(name=name, type=int(item.get('type', DEP_NODEJS)),
                           remark=item.get('remark') or '', status=DEP_QUEUED)
            s.add(d)
            s.commit()
            out.append(dep_dict(d))
            enqueue(d.id)
    return ok(out)


@router.put('')
def update_dep(payload: dict = Body(...)):
    with SessionLocal() as s:
        d = s.get(Dependence, payload.get('id'))
        if not d:
            return fail('依赖不存在', 404)
        d.name = (payload.get('name') or d.name).strip()
        d.type = int(payload.get('type', d.type))
        d.remark = payload.get('remark', d.remark)
        d.status = DEP_QUEUED
        d.log = []
        d.timestamp = str(time.strftime('%Y-%m-%d %H:%M:%S'))
        s.commit()
        out = dep_dict(d)
    enqueue(d.id if d else payload['id'])
    return ok(out)


@router.delete('')
def delete_deps(ids: list = Body(...)):
    with SessionLocal() as s:
        for i in ids:
            d = s.get(Dependence, int(i))
            if not d:
                continue
            if d.status in (DEP_INSTALLED, DEP_INSTALL_FAILED):
                d.status = DEP_REMOVING
                d.log = []
                s.commit()
                enqueue(d.id)
            else:
                s.delete(d)
        s.commit()
    return ok('请求成功')


@router.delete('/force')
def force_delete(ids: list = Body(...)):
    with SessionLocal() as s:
        s.query(Dependence).filter(Dependence.id.in_([int(i) for i in ids])).delete(synchronize_session=False)
        s.commit()
    return ok('删除成功')


@router.put('/reinstall')
def reinstall(ids: list = Body(...)):
    with SessionLocal() as s:
        for i in ids:
            d = s.get(Dependence, int(i))
            if not d:
                continue
            d.status = DEP_QUEUED
            d.log = []
            s.commit()
            enqueue(d.id)
    return ok('请求成功')


@router.put('/cancel')
def cancel(ids: list = Body(...)):
    for i in ids:
        proc = _current.get(int(i))
        if proc:
            utils.kill_process_tree(proc.pid)
        _set_status(int(i), DEP_CANCELLED)
    return ok('已取消')


@router.get('/{dep_id}')
def get_dep(dep_id: int):
    with SessionLocal() as s:
        d = s.get(Dependence, dep_id)
        return ok(dep_dict(d)) if d else fail('依赖不存在', 404)
