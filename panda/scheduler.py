"""APScheduler 封装：对应青龙 back/schedule/（node-schedule + toad-scheduler）。

- schedule 字段：5/6 段 cron 表达式、@daily 等系统别名、@boot（启动执行）、@once（仅手动）
- 时区取系统配置（青龙改 /etc/localtime，这里用 APScheduler 显式时区，行为等价）
- 无 misfire 补偿（与 node-schedule 一致，宽限 90 秒）
"""
import re

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from croniter import croniter

from . import config
from .executor import run_cron, schedule_trigger, get_system_config
from .db import SessionLocal

ALIASES = {
    '@yearly': '0 0 1 1 *',
    '@annually': '0 0 1 1 *',
    '@monthly': '0 0 1 * *',
    '@weekly': '0 0 * * 0',
    '@daily': '0 0 * * *',
    '@midnight': '0 0 * * *',
    '@hourly': '0 * * * *',
}

BOOT = '@boot'
ONCE = '@once'


def is_special_schedule(schedule: str) -> bool:
    schedule = (schedule or '').strip()
    return schedule.startswith(BOOT) or schedule.startswith(ONCE) or schedule == '@reboot'


def normalize_expr(schedule: str) -> str:
    expr = (schedule or '').strip().lower()
    expr = ALIASES.get(expr, expr)
    expr = expr.replace('?', '*')
    parts = re.split(r'\s+', expr)
    if len(parts) == 5:
        expr = '0 ' + expr  # 补秒字段 → APScheduler 6 段
    return expr


def validate_schedule(schedule: str):
    """校验 schedule 字段；返回错误消息或 None。"""
    expr = (schedule or '').strip().lower()
    if not (schedule or '').strip():
        return 'schedule 不能为空'
    if expr.startswith((BOOT, ONCE)) or expr == '@reboot':
        return None
    if expr in ALIASES:
        return None
    if re.search(r'(^|[\s,])/(\d+)', expr):
        return '不支持裸步长表达式（如 /5），请使用 */5 或 0/5'
    fields = re.split(r'\s+', expr.replace('?', '*'))
    if len(fields) == 5:
        fields = ['0'] + fields
    if len(fields) != 6:
        return 'cron 表达式需为 5 或 6 段'
    try:
        CronTrigger(
            second=fields[0], minute=fields[1], hour=fields[2],
            day=fields[3], month=fields[4], day_of_week=fields[5],
        )
        return None
    except Exception:
        return f'无效的 cron 表达式: {schedule}'


def _trigger(expr: str, tz: str) -> CronTrigger:
    fields = re.split(r'\s+', normalize_expr(expr))
    return CronTrigger(
        second=fields[0], minute=fields[1], hour=fields[2],
        day=fields[3], month=fields[4], day_of_week=fields[5], timezone=tz,
    )


class PandaScheduler:
    def __init__(self):
        self._tz = config.DEFAULT_TIMEZONE
        self.scheduler = BackgroundScheduler(timezone=self._tz)
        self.started = False

    def start(self):
        if not self.started:
            self.scheduler.start()
            self.started = True

    def set_timezone(self, tz: str):
        try:
            self.scheduler.configure(timezone=tz)
        except Exception:
            pass

    def _job_id(self, cron_id, idx=0):
        return f'cron-{cron_id}-{idx}'

    def sync_cron(self, cron):
        """注册/替换单个任务的调度（含 extra_schedules）。"""
        self.remove_cron(cron.id)
        if not cron.is_active() or is_special_schedule(cron.schedule):
            return
        exprs = [cron.schedule] + [
            e.get('schedule') for e in (cron.extra_schedules or []) if isinstance(e, dict) and e.get('schedule')
        ]
        for idx, expr in enumerate(exprs):
            if is_special_schedule(expr):
                continue
            err = validate_schedule(expr)
            if err:
                continue
            trigger = _trigger(expr, self._tz)
            self.scheduler.add_job(
                schedule_trigger, trigger, args=[cron.id],
                id=self._job_id(cron.id, idx), replace_existing=True,
                misfire_grace_time=90, coalesce=True, max_instances=10,
            )

    def remove_cron(self, cron_id):
        for idx in range(10):
            job = self.scheduler.get_job(self._job_id(cron_id, idx))
            if job:
                job.remove()

    def run_now(self, cron_id):
        self.scheduler.add_job(run_cron, args=[cron_id, None, 'schedule'], id=f'run-{cron_id}', replace_existing=True)

    def boot_tasks(self):
        """@boot：启动时执行一次（青龙 services/cron.ts bootTask）。"""
        with SessionLocal() as s:
            crons = s.query(Crontab_).filter(Crontab_.schedule.like(f'{BOOT}%')).all()
            for c in crons:
                if c.is_active():
                    schedule_trigger(c.id)

    def add_maintenance(self, days: int, fn, job_id):
        if days and days > 0:
            self.scheduler.add_job(
                fn, IntervalTrigger(days=days), id=job_id,
                replace_existing=True, misfire_grace_time=3600,
            )
        else:
            job = self.scheduler.get_job(job_id)
            if job:
                job.remove()


from .models import Crontab as Crontab_  # noqa: E402

panda_scheduler = PandaScheduler()
