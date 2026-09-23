"""系统管理 /api/system —— 版本信息/系统配置/时区/通知测试/存储保留/重启/任意命令。"""
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from .. import utils
from ..core import config
from ..services import auth, executor, notify
from ..core.common import fail, ok
from ..core.db import SessionLocal
from ..models import (
    INSTANCE_RUNNING,
    Crontab,
    CronStat,
    Dependence,
    DEP_QUEUED,
    RunningInstance,
)
from ..services.scheduler import panda_scheduler

router = APIRouter(prefix='/api/system', tags=['system'])

VERSION = '1.0.0'


@router.get('')
@router.get('/')
def system_meta():
    return ok({
        'version': VERSION,
        'publishTime': '',
        'branch': 'panda',
        'changeLog': '熊猫系统 —— 青龙面板的 Python/FastAPI 重构版',
        'changeLogLink': '',
    })


@router.get('/config')
def get_config():
    conf = auth.get_system_config_info()
    conf.setdefault('timezone', config.DEFAULT_TIMEZONE)
    return ok(conf)


def _put_config(key: str, value):
    auth.put_system_config_info({key: value})
    return ok('保存成功')


@router.put('/config/log-remove-frequency')
def log_remove_frequency(payload: dict = Body(...)):
    days = payload.get('logRemoveFrequency')
    _put_config('logRemoveFrequency', days)
    panda_scheduler.add_maintenance(int(days or 0), clean_old_logs, 'log-remove')
    return ok('保存成功')


@router.put('/config/cron-concurrency')
def cron_concurrency(payload: dict = Body(...)):
    n = payload.get('cronConcurrency')
    _put_config('cronConcurrency', n)
    executor.resize_pool()
    return ok('保存成功')


@router.put('/config/command-timeout')
def command_timeout(payload: dict = Body(...)):
    return _put_config('commandTimeout', int(payload.get('commandTimeout') or 0))


@router.put('/config/timezone')
def timezone(payload: dict = Body(...)):
    tz = payload.get('timezone') or config.DEFAULT_TIMEZONE
    try:
        import zoneinfo
        zoneinfo.ZoneInfo(tz)
    except Exception:
        return fail('无效时区', 400)
    _put_config('timezone', tz)
    _rebuild_scheduler(tz)
    return ok('保存成功')


def _rebuild_scheduler(tz: str):
    """调度器换时区：重建并重注册全部任务（青龙等效行为）。"""
    panda_scheduler.scheduler.shutdown(wait=False)
    panda_scheduler._tz = tz
    panda_scheduler.scheduler = type(panda_scheduler.scheduler)(timezone=tz)
    panda_scheduler.started = False
    panda_scheduler.start()
    with SessionLocal() as s:
        for c in s.query(Crontab).all():
            panda_scheduler.sync_cron(c)
    conf = auth.get_system_config_info()
    panda_scheduler.add_maintenance(int(conf.get('logRemoveFrequency') or 0), clean_old_logs, 'log-remove')


@router.put('/config/lang')
def lang(payload: dict = Body(...)):
    return _put_config('lang', payload.get('lang'))


@router.put('/config/panel-title')
def panel_title(payload: dict = Body(...)):
    title = (payload.get('panelTitle') or '')[:100]
    auth.put_system_config_info({'panelTitle': title})
    return ok(title)


@router.put('/config/global-ssh-key')
def global_ssh_key(payload: dict = Body(...)):
    return _put_config('globalSshKey', payload.get('globalSshKey') or '')


@router.put('/config/dependence-proxy')
def dependence_proxy(payload: dict = Body(...)):
    return _put_config('dependenceProxy', payload.get('dependenceProxy') or '')


@router.put('/config/node-mirror')
def node_mirror(payload: dict = Body(...)):
    url = payload.get('nodeMirror') or ''
    _put_config('nodeMirror', url)
    npm = utils.which_or_none('npm')
    if url and npm:
        subprocess.run([npm, 'config', 'set', 'registry', url], capture_output=True, timeout=60)
    return ok('保存成功')


@router.put('/config/python-mirror')
def python_mirror(payload: dict = Body(...)):
    url = payload.get('pythonMirror') or ''
    _put_config('pythonMirror', url)
    if url:
        subprocess.run(
            [config.PYTHON_BIN, '-m', 'pip', 'config', 'set', 'global.index-url', url],
            capture_output=True, timeout=60,
        )
    return ok('保存成功')


@router.put('/config/linux-mirror')
def linux_mirror(payload: dict = Body(...)):
    return _put_config('linuxMirror', payload.get('linuxMirror') or '')


@router.put('/config/dependence-clean')
def dependence_clean(payload: dict = Body(...)):
    return ok('熊猫系统的 Python 依赖直接安装于虚拟环境，无需缓存清理')


@router.put('/notify')
def send_notify_api(payload: dict = Body(...)):
    sent = notify.send_notify(payload.get('content') or '', payload.get('title') or '熊猫系统')
    return ok({'sent': sent})


@router.put('/reload')
def reload_system(payload: dict = Body(default={})):
    """重启面板进程（Docker/supervisor/服务管理器拉起时生效）。"""
    from ..services.ws import ws_manager
    ws_manager.broadcast('reloadSystem', '系统正在重启')

    def _restart():
        time.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    import threading
    threading.Thread(target=_restart, daemon=True).start()
    return ok('重启中')


@router.put('/command-run')
def command_run(payload: dict = Body(...)):
    command = payload.get('command') or ''
    if not command:
        return fail('命令不能为空', 400)
    bash = utils.find_bash()
    argv = [bash, '-c', command] if bash else (
        ['cmd', '/c', command] if config.IS_WINDOWS else ['/bin/sh', '-c', command]
    )
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(config.DATA_DIR), text=True, encoding='utf-8', errors='replace',
        )
    except Exception as e:
        return fail(str(e), 500)
    output = []
    for line in iter(proc.stdout.readline, ''):
        output.append(line)
    code = proc.wait()
    return ok({'exitCode': code, 'output': ''.join(output)})


# ---------- 存储保留（retention） ----------

@router.put('/storage-retention/config')
def retention_config(payload: dict = Body(...)):
    ri = payload.get('runningInstanceRetentionDays')
    cs = payload.get('cronStatRetentionDays')
    for v in (ri, cs):
        if v is not None and not (0 <= int(v) <= 3650):
            return fail('保留天数需在 0-3650 之间', 400)
    auth.put_system_config_info({
        'runningInstanceRetentionDays': ri, 'cronStatRetentionDays': cs,
    })
    return ok('保存成功')


@router.get('/storage-retention/config')
def retention_get():
    conf = auth.get_system_config_info()
    return ok({
        'runningInstanceRetentionDays': conf.get('runningInstanceRetentionDays'),
        'cronStatRetentionDays': conf.get('cronStatRetentionDays'),
    })


def _retention_counts():
    conf = auth.get_system_config_info()
    now = int(time.time())
    ri_days = conf.get('runningInstanceRetentionDays')
    cs_days = conf.get('cronStatRetentionDays')
    with SessionLocal() as s:
        ri = 0
        if ri_days:
            cutoff = now - int(ri_days) * 86400
            ri = s.query(RunningInstance).filter(
                RunningInstance.status != INSTANCE_RUNNING
            ).filter(
                (RunningInstance.finished_at < cutoff)
                | (RunningInstance.finished_at.is_(None) & (RunningInstance.started_at < cutoff))
            ).count()
        cs = 0
        if cs_days:
            cutoff_date = (datetime.now() - timedelta(days=int(cs_days))).strftime('%Y-%m-%d')
            cs = s.query(CronStat).filter(CronStat.date < cutoff_date).count()
        return {'runningInstances': ri, 'cronStats': cs}


@router.post('/storage-retention/preview')
def retention_preview(payload: dict = Body(default={})):
    return ok(_retention_counts())


@router.post('/storage-retention/cleanup')
def retention_cleanup(payload: dict = Body(...)):
    if payload.get('confirmation') != 'CLEAN':
        return fail('需要 confirmation=CLEAN', 400)
    conf = auth.get_system_config_info()
    now = int(time.time())
    with SessionLocal() as s:
        ri_days = conf.get('runningInstanceRetentionDays')
        if ri_days:
            cutoff = now - int(ri_days) * 86400
            s.query(RunningInstance).filter(
                RunningInstance.status != INSTANCE_RUNNING
            ).filter(
                (RunningInstance.finished_at < cutoff)
                | (RunningInstance.finished_at.is_(None) & (RunningInstance.started_at < cutoff))
            ).delete(synchronize_session=False)
        cs_days = conf.get('cronStatRetentionDays')
        if cs_days:
            cutoff_date = (datetime.now() - timedelta(days=int(cs_days))).strftime('%Y-%m-%d')
            s.query(CronStat).filter(CronStat.date < cutoff_date).delete(synchronize_session=False)
        s.commit()
        if payload.get('compactDatabase') and config.DATABASE_URL.startswith('sqlite'):
            from sqlalchemy import text
            s.execute(text('VACUUM'))
    return ok('清理完成')


def clean_old_logs():
    """日志保留策略（青龙 ql rmlog）：按文件名日期删除，被任务引用的日志跳过。"""
    conf = auth.get_system_config_info()
    days = int(conf.get('logRemoveFrequency') or 0)
    if not days:
        return
    cutoff = time.time() - days * 86400
    with SessionLocal() as s:
        referenced = {c.log_path for c in s.query(Crontab).filter(Crontab.log_path != '')}
    for fp in config.LOG_PATH.rglob('*.log'):
        try:
            rel = str(fp.relative_to(config.LOG_PATH)).replace('\\', '/')
            name = fp.name
            file_date = datetime.strptime(name[:10], '%Y-%m-%d')
            ts = file_date.timestamp()
        except Exception:
            ts = fp.stat().st_mtime
        if ts < cutoff and rel not in referenced:
            fp.unlink(missing_ok=True)
    for d in sorted(config.LOG_PATH.rglob('*'), reverse=True):
        if d.is_dir() and not any(d.iterdir()) and d.name != '.tmp':
            d.rmdir()


@router.put('/auth/reset')
def auth_reset(payload: dict = Body(default={})):
    """命令行式重置账号（青龙 CLI 等效接口）。"""
    info = auth.get_auth_info()
    if 'retries' in payload:
        info['retries'] = int(payload['retries'])
    if payload.get('twoFactorActivated') is False:
        info['twoFactorActivated'] = False
        info['twoFactorSecret'] = ''
    if payload.get('password'):
        info['password'] = security_hash(payload['password'])
    if payload.get('username'):
        info['username'] = payload['username']
    info['tokens'] = {'desktop': [], 'mobile': []}
    info['token'] = None
    auth.save_auth_info(info)
    return ok('重置成功')


def security_hash(pw: str) -> str:
    from ..core import security
    return security.hash_password(pw)

