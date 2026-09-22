"""任务执行引擎：对齐青龙 shell/task.sh + services/cron.ts 语义。

- 解释器映射: .js/.mjs/.cjs→node, .py→虚拟环境 python, .sh→bash, .ts→tsx/ts-node, .ps1→powershell
- 命令前缀 task/ql 自动剥离；`-m <秒>` 单任务超时；`now` 立即语义；`conc ENV 范围`/`desi ENV 序号` 多账号并发
- 日志: data/log/<uniqPath>/<YYYY-MM-DD-HH-mm-ss-SSS>.log，real_time 由本进程独占流式写入
- 状态机: queued --CAS(queued_token)--> running --> idle；RunningInstance/CrontabStat 同步维护
"""
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import config, envsync, utils
from .db import SessionLocal
from .models import (
    CRON_IDLE,
    CRON_QUEUED,
    CRON_RUNNING,
    INSTANCE_ERROR,
    INSTANCE_FINISHED,
    INSTANCE_RUNNING,
    INSTANCE_STOPPED,
    Crontab,
    CronStat,
    RunningInstance,
    SystemRow,
)

LOG_END_SYMBOL = '\u3000' * 5
logger = logging.getLogger('panda.executor')
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_queued_crons: dict = {}  # cron_id -> 排队中的触发次数
_active_procs: dict = {}   # cron_id -> [_ProcHandle]
_stopped_runs: set = set()  # 被主动停止的 cron_id


def get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            with SessionLocal() as s:
                conf = get_system_config(s)
            n = int(conf.get('cronConcurrency') or 0) or max((os.cpu_count() or 4), 4)
            _pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix='cron')
        return _pool


def resize_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=False)
            _pool = None


def get_system_config(session) -> dict:
    row = session.query(SystemRow).filter(SystemRow.type == 'systemConfig').first()
    return dict(row.info or {}) if row else {}


def build_env(session, extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    try:
        for k, v in envsync.group_envs(session).items():
            env[k] = v
    except Exception:
        pass
    env['PD_DIR'] = str(config.PD_DIR)
    env['QL_DIR'] = str(config.PD_DIR)
    env['QL_DATA_DIR'] = str(config.DATA_DIR)
    env['PD_TASK_ID'] = str((extra or {}).get('PD_TASK_ID', ''))
    env['PYTHONPATH'] = os.pathsep.join(
        [str(config.PRELOAD_PATH), env.get('PYTHONPATH', '')]
    ).strip(os.pathsep)
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


def _script_target(token: str):
    """命令中的脚本 token → data/scripts 下的绝对路径（若存在）。"""
    cleaned = (token or '').strip('"\'' )
    if not cleaned or cleaned.startswith(('-', 'http')):
        return None
    p = Path(cleaned)
    if p.is_absolute():
        candidate = p
    else:
        candidate = config.SCRIPT_PATH / p
    if candidate.is_file():
        return candidate
    return None


def _argv_for_script(path: Path, args, session):
    ext = path.suffix.lower()
    if ext in ('.py', '.pyc'):
        return [config.PYTHON_BIN, str(path)] + args
    if ext in ('.js', '.mjs', '.cjs'):
        node = utils.which_or_none('node')
        if not node:
            raise FileNotFoundError('未找到 node 解释器，无法执行 JS 脚本')
        return [node, str(path)] + args
    if ext == '.ts':
        for runner, pre in (('tsx', ['tsx']), ('ts-node', ['ts-node', '--transpile-only'])):
            w = utils.which_or_none(runner)
            if w:
                return [w] + pre + [str(path)] + args
        raise FileNotFoundError('未找到 tsx/ts-node，无法执行 TS 脚本')
    if ext == '.sh':
        bash = utils.find_bash()
        if not bash:
            raise FileNotFoundError('未找到 bash，无法执行 Shell 脚本（Windows 需安装 Git Bash 并加入 PATH）')
        return [bash, str(path)] + args
    if ext == '.ps1':
        ps = utils.which_or_none('powershell') or utils.which_or_none('pwsh')
        if not ps:
            raise FileNotFoundError('未找到 PowerShell')
        return [ps, '-ExecutionPolicy', 'Bypass', '-File', str(path)] + args
    return [str(path)] + args


RANGE_RE = re.compile(r'^(\d+)(?:-(\d+|max))?$')


def _parse_indices(spec: str, total: int):
    spec = (spec or '1-max').replace(',', ' ')
    idxs = []
    for part in spec.split():
        m = RANGE_RE.match(part)
        if m:
            lo = int(m.group(1))
            hi = total if m.group(2) in ('max', None) else int(m.group(2))
            idxs += list(range(lo, hi + 1))
        elif part.isdigit():
            idxs.append(int(part))
    return [i for i in idxs if 1 <= i <= total]


class _ProcHandle:
    """暴露给停止流程的进程句柄。"""

    def __init__(self):
        self.procs = []
        self.pids = []

    def add(self, proc):
        self.procs.append(proc)
        self.pids.append(proc.pid)


def _run_conc_desi(mode, env_key, spec, base_argv, env, cwd, log_f, deadline, handle):
    values = (env.get(env_key) or '').split('&') if env.get(env_key) else []
    if not values or values == ['']:
        msg = f'环境变量 {env_key} 不存在或为空，无法执行 {mode} 模式\n'
        if log_f:
            log_f.write(msg.encode('utf-8', 'ignore'))
        return 1
    if mode == 'conc':
        indices = _parse_indices(spec, len(values))
    else:
        indices = [int(x) for x in re.split(r'[, ]+', spec or '') if x.isdigit() and 1 <= int(x) <= len(values)]
    codes = {}

    def worker(i):
        e = dict(env)
        e[env_key] = values[i - 1]
        tmp = Path(str(log_f.name) + f'.{i}.tmp') if log_f else None
        tf = open(tmp, 'wb') if tmp else None
        try:
            code, pid = _run_proc_tracked(base_argv, e, cwd, tf, deadline, handle)
            codes[i] = code
        finally:
            if tf:
                tf.close()
        return tmp

    threads = [threading.Thread(target=lambda i=i: worker(i)) for i in indices]
    [t.start() for t in threads]
    [t.join() for t in threads]
    for i in indices:  # 按青龙 otask.sh 顺序合并
        tmp = Path(str(log_f.name) + f'.{i}.tmp') if log_f else None
        if tmp and tmp.exists():
            if log_f:
                log_f.write(tmp.read_bytes())
                log_f.flush()
            tmp.unlink(missing_ok=True)
    return 0 if all(c == 0 for c in codes.values()) else 1


def _run_proc_tracked(argv, env, cwd, log_f, deadline, handle: _ProcHandle):
    proc = subprocess.Popen(
        argv, cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    handle.add(proc)
    code = None
    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            if log_f:
                log_f.write(line)
                log_f.flush()
        code = proc.wait()
    except Exception:
        code = proc.wait()
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        if proc in handle.procs:
            handle.procs.remove(proc)
    return code, proc.pid


def _apply_task_code(code_snippet: str, env, cwd, log_f, phase: str):
    """task_before/task_after：以 bash 执行内联代码（青龙把 \n 折成 ;）。"""
    bash = utils.find_bash()
    snippet = (code_snippet or '').replace('\n', ';').strip()
    if not snippet:
        return 0
    if not bash:
        if log_f:
            log_f.write(f'[警告] 未找到 bash，跳过 task_{phase} 代码\n'.encode())
        return 0
    proc = subprocess.Popen(
        [bash, '-c', snippet], cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE if log_f else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    if log_f and proc.stdout:
        for line in iter(proc.stdout.readline, b''):
            log_f.write(line)
        proc.stdout.close()
    return proc.wait()


def run_cron(cron_id: int, queued_token: str = None, source: str = 'manual'):
    """执行一次任务。queued_token 非空时执行手动 CAS 声明；调度触发传 None。"""
    with SessionLocal() as s:
        cron = s.get(Crontab, cron_id)
        if not cron:
            return
        conf = get_system_config(s)

        if queued_token is not None:
            ok = (
                s.query(Crontab)
                .filter(
                    Crontab.id == cron_id,
                    Crontab.status == CRON_QUEUED,
                    Crontab.queued_token == queued_token,
                )
                .update({'status': CRON_RUNNING})
            )
            s.commit()
            if not ok:
                return
        else:
            # 调度触发：单实例互斥 —— 先杀旧进程
            if not cron.allow_multiple_instances:
                _kill_running_instances(s, cron)
            s.query(Crontab).filter(Crontab.id == cron_id).update({'status': CRON_RUNNING})
            s.commit()

        s2 = SessionLocal()
        cron = s2.get(Crontab, cron_id)
        try:
            _execute(s2, cron, conf, queued_token, source)
        except Exception:
            logger.exception('任务 %s 执行异常', cron_id)
            try:
                s2.query(Crontab).filter(Crontab.id == cron_id).update({'status': CRON_IDLE})
                s2.commit()
            except Exception:
                s2.rollback()
        finally:
            s2.close()


def _kill_running_instances(session, cron):
    instances = (
        session.query(RunningInstance)
        .filter(RunningInstance.cron_id == cron.id, RunningInstance.status == INSTANCE_RUNNING)
        .all()
    )
    pids = {i.pid for i in instances if i.pid}
    if cron.pid:
        pids.add(cron.pid)
    for pid in pids:
        utils.kill_process_tree(pid)
    for i in instances:
        i.status = INSTANCE_STOPPED
        i.finished_at = int(time.time())
    session.commit()


def _execute(session, cron: Crontab, conf: dict, queued_token, source):
    tokens = utils.shell_split(cron.command or '')
    if tokens and tokens[0] in ('task', 'ql'):
        tokens = tokens[1:]
    timeout = 0
    if len(tokens) > 1 and tokens[0] == '-m' and tokens[1].isdigit():
        timeout = int(tokens[1])
        tokens = tokens[2:]

    uniq = get_uniq_log_dir(session, cron)
    discard = uniq is None
    log_path_rel = ''
    log_file = None
    log_f = None
    if not discard:
        log_ts = utils.now_ts_ms()
        log_path_rel = f'{uniq}/{log_ts}.log'
        log_file = config.LOG_PATH / uniq / f'{log_ts}.log'
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_file, 'ab')

    begin = int(time.time())
    env = build_env(session, {'PD_TASK_ID': str(cron.id), 'QL_TASK_ID': str(cron.id)})
    work_dir = _resolve_work_dir(cron)

    instance = RunningInstance(cron_id=cron.id, log_path=log_path_rel, started_at=begin)
    session.add(instance)
    cron.status = CRON_RUNNING
    cron.log_path = log_path_rel or cron.log_path
    cron.last_execution_time = begin
    cron.queued_token = None
    session.commit()

    exit_code = 1
    handle = _ProcHandle()
    _active_procs.setdefault(cron.id, []).append(handle)
    _stopped_runs.discard(cron.id)

    try:
        script_token = tokens[0] if tokens else ''
        script = _script_target(script_token)
        args = [a for a in tokens[1:]]
        if script:
            if args and args[0] == 'now':
                args = args[1:]
            deadline = (begin + timeout) if timeout else 0
            if cron.task_before:
                _apply_task_code(cron.task_before, env, work_dir, log_f, 'before')
            if args and args[0] in ('conc', 'desi') and len(args) >= 2:
                base_argv = _argv_for_script(script, _strip_mode(args), session)
                exit_code = _run_conc_desi(
                    args[0], args[1], args[2] if len(args) > 2 else '1-max',
                    base_argv, env, work_dir, log_f, deadline, handle,
                )
            else:
                argv = _argv_for_script(script, args, session)
                code, pid = _run_with_deadline(argv, env, work_dir, log_f, deadline, handle, instance, session)
                exit_code = code
            if cron.task_after:
                _apply_task_code(cron.task_after, env, work_dir, log_f, 'after')
        elif tokens:
            # 普通命令透传（等价 otask.sh run_else）
            argv = _resolve_generic(tokens)
            deadline = (begin + timeout) if timeout else 0
            code, pid = _run_with_deadline(argv, env, work_dir, log_f, deadline, handle, instance, session)
            exit_code = code
        else:
            if log_f:
                log_f.write('[错误] 任务命令为空\n'.encode('utf-8'))
            exit_code = 1
    except Exception as e:
        msg = f'[熊猫系统] 执行失败: {e}\n'.encode('utf-8', 'ignore')
        if log_f:
            log_f.write(msg)
        else:
            sys.stderr.buffer.write(msg)
        exit_code = 1
    finally:
        handles = _active_procs.get(cron.id) or []
        if handle in handles:
            handles.remove(handle)
        stopped = cron.id in _stopped_runs
        if log_f:
            dur = int(time.time()) - begin
            if stopped:
                tail = f'## 已停止 🛑 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} {LOG_END_SYMBOL}\n'
            elif exit_code == 0:
                tail = f'## 完成 ✅ {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}  耗时 {dur} 秒 {LOG_END_SYMBOL}\n'
            else:
                tail = f'## 失败 ❌(退出码 {exit_code}) {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}  耗时 {dur} 秒 {LOG_END_SYMBOL}\n'
            log_f.write(tail.encode('utf-8'))
            log_f.flush()
            log_f.close()
        if handle.pids:
            instance.pid = handle.pids[0]
        _finish(session, cron, instance, exit_code, begin, stopped_flag=stopped)
        _stopped_runs.discard(cron.id)


def _strip_mode(args):
    return args[3:] if args and args[0] in ('conc', 'desi') else args


def _resolve_generic(tokens):
    exe = utils.which_or_none(tokens[0]) or (tokens[0] if not config.IS_WINDOWS else f'{tokens[0]}.cmd' if utils.which_or_none(f'{tokens[0]}.cmd') else tokens[0])
    return [exe] + tokens[1:]


def _run_with_deadline(argv, env, cwd, log_f, deadline, handle, instance, session):
    proc = subprocess.Popen(
        argv, cwd=str(cwd) if cwd else None, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    handle.add(proc)
    instance.pid = proc.pid
    session.commit()
    code = None
    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            if log_f:
                log_f.write(line)
                log_f.flush()
            if deadline and time.time() > deadline:
                utils.kill_process_tree(proc.pid)
                if log_f:
                    log_f.write('[错误] 任务超时，已终止\n'.encode('utf-8'))
                code = 124
                break
        if code is None:
            code = proc.wait()
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        if proc in handle.procs:
            handle.procs.remove(proc)
    return code, proc.pid


def _resolve_work_dir(cron: Crontab):
    if cron.work_dir:
        p = Path(cron.work_dir)
        if not p.is_absolute():
            p = config.SCRIPT_PATH / cron.work_dir
        if p.is_dir():
            return p
    return config.SCRIPT_PATH


def get_uniq_log_dir(session, cron: Crontab):
    name = (cron.log_name or '').strip()
    if name == '/dev/null':
        return None
    if name:
        try:
            utils.resolve_file_access(config.LOG_PATH, [name])
            return name.replace('\\', '/').strip('/')
        except PermissionError:
            return utils.get_uniq_path(cron.command, cron.id)
    return utils.get_uniq_path(cron.command, cron.id)


def _finish(session, cron: Crontab, instance: RunningInstance, exit_code, begin, stopped_flag=False):
    dur_ms = int((time.time() - begin) * 1000)
    now = int(time.time())
    if stopped_flag:
        instance.status = INSTANCE_STOPPED
    elif exit_code == 0:
        instance.status = INSTANCE_FINISHED
    else:
        instance.status = INSTANCE_ERROR
    instance.finished_at = now
    instance.exit_code = exit_code
    cron.status = CRON_IDLE
    cron.pid = None
    cron.last_running_time = now - begin
    session.flush()

    date = datetime.now().strftime('%Y-%m-%d')
    stat = (
        session.query(CronStat)
        .filter(CronStat.ref_id == cron.id, CronStat.date == date)
        .first()
    )
    if not stat:
        stat = CronStat(ref_id=cron.id, date=date)
        session.add(stat)
    # 新建行的列 default 要到 INSERT 才生效，先按 0 处理
    stat.run_count = (stat.run_count or 0) + 1
    if exit_code == 0:
        stat.success_count = (stat.success_count or 0) + 1
    else:
        stat.fail_count = (stat.fail_count or 0) + 1
    stat.total_time = (stat.total_time or 0) + dur_ms
    stat.max_time = max(stat.max_time or 0, dur_ms)
    session.commit()


def schedule_trigger(cron_id: int):
    """APScheduler 回调入口：排队去重（同任务排队>5 丢弃）。"""
    cnt = _queued_crons.get(cron_id, 0)
    if cnt >= 5:
        s = SessionLocal()
        try:
            cron = s.get(Crontab, cron_id)
            name = cron.name or cron.command if cron else str(cron_id)
        finally:
            s.close()
        from .notify import send_notify
        send_notify('任务重复运行', f'任务 [{name}] 等待队列超过 5 个，本次调度已忽略')
        return
    _queued_crons[cron_id] = cnt + 1

    def _wrapped():
        try:
            run_cron(cron_id, queued_token=None, source='schedule')
        finally:
            _queued_crons[cron_id] = max(_queued_crons.get(cron_id, 1) - 1, 0)

    get_pool().submit(_wrapped)


def manual_run(cron_id: int, queued_token: str):
    get_pool().submit(run_cron, cron_id, queued_token, 'manual')


def stop_cron(cron_id: int):
    """停止任务：杀掉全部运行实例进程树并回写状态。"""
    _stopped_runs.add(cron_id)
    with SessionLocal() as s:
        cron = s.get(Crontab, cron_id)
        if not cron:
            return
        instances = (
            s.query(RunningInstance)
            .filter(RunningInstance.cron_id == cron_id, RunningInstance.status == INSTANCE_RUNNING)
            .all()
        )
        pids = {i.pid for i in instances if i.pid}
        if cron.pid:
            pids.add(cron.pid)
        for pid in pids:
            utils.kill_process_tree(pid)
        now = int(time.time())
        for i in instances:
            i.status = INSTANCE_STOPPED
            i.finished_at = now
            i.exit_code = 143
        # 未被执行的 queued 声明直接收回
        if cron.status in (CRON_QUEUED, CRON_RUNNING) and not pids:
            cron.status = CRON_IDLE
        cron.pid = None
        s.commit()
