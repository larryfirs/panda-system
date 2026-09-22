"""看板 /api/dashboard —— 今日统计/趋势/排行/运行中/系统信息（青龙 dashboard API 契约）。"""
import os
import platform
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Query

from ..common import ok
from ..db import SessionLocal
from ..models import (
    CRON_DISABLED,
    CRON_QUEUED,
    INSTANCE_RUNNING,
    Crontab,
    CronStat,
    RunningInstance,
)

router = APIRouter(prefix='/api/dashboard', tags=['dashboard'])


def today() -> str:
    return datetime.now().strftime('%Y-%m-%d')


@router.post('/record')
def record(payload: dict = Body(...)):
    ref_id = int(payload.get('ref_id') or 0)
    code = int(payload.get('code', 0))
    elapsed = float(payload.get('elapsed') or 0)
    elapsed_ms = int(elapsed * 1000)
    with SessionLocal() as s:
        stat = (
            s.query(CronStat)
            .filter(CronStat.ref_id == ref_id, CronStat.date == today())
            .first()
        )
        if not stat:
            stat = CronStat(ref_id=ref_id, date=today())
            s.add(stat)
        stat.run_count += 1
        if code == 0:
            stat.success_count += 1
        else:
            stat.fail_count += 1
        stat.total_time += elapsed_ms
        stat.max_time = max(stat.max_time, elapsed_ms)
        s.commit()
    return ok('记录成功')


@router.get('/overview')
def overview():
    with SessionLocal() as s:
        total = s.query(Crontab).count()
        enabled = s.query(Crontab).filter(Crontab.isDisabled == 0, Crontab.status != CRON_DISABLED).count()
        stat = (
            s.query(CronStat)
            .filter(CronStat.date == today())
            .with_entities(
                CronStat.run_count, CronStat.success_count, CronStat.fail_count, CronStat.total_time
            )
            .all()
        )
        runs = sum(x[0] or 0 for x in stat)
        success = sum(x[1] or 0 for x in stat)
        fail = sum(x[2] or 0 for x in stat)
        total_time = sum(x[3] or 0 for x in stat)
        return ok({
            'total': total,
            'enabled': enabled,
            'disabled': total - enabled,
            'todayRuns': runs,
            'todaySuccess': success,
            'todayFail': fail,
            'successRate': f'{(success / runs * 100):.1f}' if runs else '0.0',
            'avgTime': round(total_time / runs) if runs else 0,
        })


def _top_stats(fail_only: bool):
    with SessionLocal() as s:
        rows = (
            s.query(CronStat)
            .filter(CronStat.date == today())
            .all()
        )
        out = []
        for r in rows:
            metric = r.fail_count if fail_only else r.success_count
            if metric <= 0:
                continue
            cron = s.get(Crontab, r.ref_id)
            out.append({
                'ref_id': r.ref_id,
                'name': cron.name if cron else f'已删除任务(#{r.ref_id})',
                'deleted': not cron,
                'count': metric,
                'total': r.run_count,
                'avgTime': round(r.total_time / r.run_count) if r.run_count else 0,
                'maxTime': r.max_time,
            })
        out.sort(key=lambda x: -x['count'])
        return out


@router.get('/successes')
def successes():
    return ok(_top_stats(False))


@router.get('/failures')
def failures():
    return ok(_top_stats(True))


@router.get('/trend')
def trend(days: int = Query(7)):
    with SessionLocal() as s:
        out = []
        for i in range(days - 1, -1, -1):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            rows = s.query(CronStat).filter(CronStat.date == date).all()
            out.append({
                'date': date[5:],
                'total': sum(r.run_count for r in rows),
                'success': sum(r.success_count for r in rows),
                'fail': sum(r.fail_count for r in rows),
            })
        return ok(out)


@router.get('/top-time')
def top_time():
    with SessionLocal() as s:
        rows = s.query(CronStat).filter(CronStat.date == today()).all()
        items = []
        for r in rows:
            cron = s.get(Crontab, r.ref_id)
            items.append({
                'id': r.ref_id,
                'name': cron.name if cron else str(r.ref_id),
                'avgTime': round(r.total_time / r.run_count) if r.run_count else 0,
                'maxTime': r.max_time,
            })
        items.sort(key=lambda x: -x['avgTime'])
        return ok(items[:5])


@router.get('/top-count')
def top_count():
    with SessionLocal() as s:
        rows = s.query(CronStat).filter(CronStat.date == today()).all()
        items = []
        for r in rows:
            cron = s.get(Crontab, r.ref_id)
            items.append({
                'id': r.ref_id,
                'name': cron.name if cron else str(r.ref_id),
                'count': r.run_count,
            })
        items.sort(key=lambda x: -x['count'])
        return ok(items[:5])


@router.get('/runtime')
def runtime():
    now = int(time.time())
    with SessionLocal() as s:
        running = s.query(RunningInstance).filter(RunningInstance.status == INSTANCE_RUNNING).all()
        queued = s.query(Crontab).filter(Crontab.status == CRON_QUEUED).count()
        running_list = []
        for r in running:
            cron = s.get(Crontab, r.cron_id)
            running_list.append({
                'id': r.id,
                'cron_id': r.cron_id,
                'name': cron.name if cron else str(r.cron_id),
                'pid': r.pid,
                'log_path': r.log_path,
                'duration': now - r.started_at,
            })
        idle_rows = s.query(Crontab).filter(
            Crontab.isDisabled == 0,
            Crontab.status != CRON_DISABLED,
            Crontab.last_execution_time < now - 86400,
        ).all()
        idle = [{
            'id': c.id, 'name': c.name,
            'lastExecute': (datetime.fromtimestamp(c.last_execution_time).strftime('%Y-%m-%d %H:%M:%S')
                             if c.last_execution_time else '从未执行'),
        } for c in idle_rows[:50]]
        return ok({
            'runningCount': len(running_list),
            'queuedCount': queued,
            'running': running_list,
            'idleTasks': idle,
        })


@router.get('/labels')
def labels():
    with SessionLocal() as s:
        agg = {}
        for c in s.query(Crontab).all():
            tags = c.labels or ['未分类']
            for t in tags:
                item = agg.setdefault(t, {'label': t, 'total': 0, 'enabled': 0, 'running': 0})
                item['total'] += 1
                if c.isDisabled == 0:
                    item['enabled'] += 1
                if c.status == 0:
                    item['running'] += 1
        return ok(list(agg.values()))


@router.get('/system')
def system_info():
    info = {
        'platform': f'{platform.system()} {platform.release()} ({platform.machine()})',
        'uptime': int(time.time() - psutil_boot_time()) if _HAS_PSUTIL else 0,
        'memTotal': 0, 'memFree': 0, 'memUsagePercent': 0,
        'heapUsed': 0, 'heapTotal': 0,
        'loadAvg': list(os.getloadavg()) if hasattr(os, 'getloadavg') else [],
        'cpus': os.cpu_count(),
    }
    if _HAS_PSUTIL:
        vm = psutil.virtual_memory()
        info.update({'memTotal': vm.total, 'memFree': vm.available, 'memUsagePercent': vm.percent})
    try:
        import sys
        info['python'] = sys.version.split()[0]
        info['pythonVersion'] = sys.version.split()[0]
    except Exception:
        pass
    return ok(info)


try:
    import psutil
    _HAS_PSUTIL = True

    def psutil_boot_time():
        return psutil.boot_time()
except ImportError:
    _HAS_PSUTIL = False

    def psutil_boot_time():
        return time.time()
