"""启动初始化（青龙 loaders/initFile.ts + initData.ts 的 Python 等效实现）。"""
import logging
import shutil
import sys
import time
from logging.handlers import TimedRotatingFileHandler

from . import config
from ..services import auth, envsync
from .db import Base, SessionLocal, engine
from ..models import (
    CRON_IDLE,
    DEP_QUEUED,
    INSTANCE_RUNNING,
    INSTANCE_STOPPED,
    VIEW_SYSTEM,
    Crontab,
    CronView,
    Dependence,
    INIT_POSITION,
    RunningInstance,
)

log = logging.getLogger('panda')


def setup_logging():
    config.SYSLOG_PATH.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    fh = TimedRotatingFileHandler(
        config.SYSLOG_PATH / 'panda.log', when='midnight', backupCount=7, encoding='utf-8'
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def seed_files():
    """目录与种子文件初始化。"""
    config.ensure_dirs()
    sample = config.SAMPLES_DIR
    targets = {
        'config.sample.sh': config.CONFIG_PATH / 'config.sh',
        'notify.js': config.SCRIPT_PATH / 'sendNotify.js',
        'notify.py': config.SCRIPT_PATH / 'notify.py',
    }
    for name, target in targets.items():
        src = sample / name
        if src.exists() and not target.exists():
            shutil.copy2(src, target)
    (config.CONFIG_PATH / 'task_before.sh').touch(exist_ok=True)
    (config.CONFIG_PATH / 'task_after.sh').touch(exist_ok=True)


def init_data():
    """建表 + 默认数据 + 状态恢复 + 调度注册。"""
    Base.metadata.create_all(engine)

    auth.get_auth_info()  # 保证 authConfig 行存在
    auth.get_system_config_row()
    auth.get_notification_info()
    auth.get_system_token()

    with SessionLocal() as s:
        # 默认系统视图（青龙 initData：全部任务）
        if not s.query(CronView).filter(
            CronView.type == VIEW_SYSTEM, CronView.name == '全部任务'
        ).first():
            s.add(CronView(name='全部任务', position=INIT_POSITION / 2, type=VIEW_SYSTEM,
                           filters=[], sorts=[], filterRelation='and'))
            s.commit()

        # 重启恢复：所有任务回 idle、running 实例置 stopped（青龙 initData）
        s.query(Crontab).update({'status': CRON_IDLE, 'queued_token': None}, synchronize_session=False)
        s.query(RunningInstance).filter(RunningInstance.status == INSTANCE_RUNNING).update(
            {'status': INSTANCE_STOPPED, 'finished_at': int(time.time())}, synchronize_session=False
        )
        s.commit()

        envsync.set_envs(s)

    from ..routers.cron import render_crontab_list
    render_crontab_list()


def start_scheduler():
    from ..services.scheduler import panda_scheduler
    conf = auth.get_system_config_info()
    panda_scheduler._tz = conf.get('timezone') or config.DEFAULT_TIMEZONE
    panda_scheduler.scheduler = type(panda_scheduler.scheduler)(timezone=panda_scheduler._tz)
    panda_scheduler.start()

    with SessionLocal() as s:
        for c in s.query(Crontab).all():
            panda_scheduler.sync_cron(c)
        # 依赖重启重排（青龙 initData：queued 串行重装）
        deps = s.query(Dependence).filter(
            Dependence.status.in_([0, 3, 6])
        ).all()
        ids = []
        for d in deps:
            d.status = DEP_QUEUED
            ids.append(d.id)
        s.commit()

    from ..routers import dependence as dep_router
    for i in ids:
        dep_router.enqueue(i)

    days = int(conf.get('logRemoveFrequency') or 0)
    panda_scheduler.add_maintenance(days, _clean_logs, 'log-remove')
    ri_days = int(conf.get('runningInstanceRetentionDays') or 0)
    cs_days = int(conf.get('cronStatRetentionDays') or 0)
    if ri_days or cs_days:
        panda_scheduler.add_maintenance(1, _clean_retention, 'retention')
    panda_scheduler.boot_tasks()


def _clean_logs():
    from ..routers.system import clean_old_logs
    clean_old_logs()


def _clean_retention():
    from ..routers.system import retention_cleanup
    from fastapi import Body  # noqa
    try:
        retention_cleanup({'confirmation': 'CLEAN'})
    except Exception:
        log.exception('retention cleanup failed')
