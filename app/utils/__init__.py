"""通用工具：路径安全、日志命名（青龙 getUniqPath 算法）、目录树、系统探测。"""
import os
import re
import shlex
import shutil
import sys
from datetime import datetime
from pathlib import Path

from ..core import config

ENV_NAME_RE = re.compile(r'^[a-zA-Z_][0-9a-zA-Z_]*$')


def now_ts_ms() -> str:
    now = datetime.now()
    return now.strftime('%Y-%m-%d-%H-%M-%S-') + f'{now.microsecond // 1000:03d}'


def resolve_file_access(base: Path, parts, blacklist=()) -> Path:
    """把相对路径片段安全地解析到 base 之下，拒绝穿越/黑名单/软链逃逸。"""
    base = Path(base).resolve()
    target = base
    for part in parts:
        if not part:
            continue
        p = Path(str(part).replace('\\', '/').lstrip('/'))
        if '..' in p.parts:
            raise PermissionError('路径越界')
        target = (target / p).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise PermissionError('路径越界')
    name = target.name
    if name in blacklist or any(b in target.parts for b in blacklist):
        raise PermissionError('暂无权限')
    if target.is_symlink():
        raise PermissionError('暂无权限')
    return target


def get_uniq_path(command: str, cron_id) -> str:
    """青龙 config/util.ts getUniqPath：由命令推出日志目录名。"""
    command = (command or '').strip()
    tokens = shell_split(command)
    i = 0
    if tokens and tokens[0] in ('task', 'ql'):
        i = 1
    if i < len(tokens) and tokens[i] == '-m':
        i += 2
    target = tokens[i] if i < len(tokens) else command
    target = target.split('?')[0]
    base = Path(target)
    name = base.name
    if name.endswith(('.js', '.py', '.sh', '.ts', '.mjs', '.cjs', '.pyc', '.ps1')):
        name = name.rsplit('.', 1)[0]
    if '/' in target and len(base.parts) >= 2:
        name = '_'.join(base.parts[-2:])
        if name.endswith(('.js', '.py', '.sh', '.ts', '.mjs', '.cjs', '.pyc', '.ps1')):
            name = name.rsplit('.', 1)[0]
    if re.match(r'^\d+$', str(cron_id)):
        name = f'{name}_{cron_id}'
    return name


def shell_split(command: str):
    posix = not config.IS_WINDOWS
    try:
        return [t.strip('"\'') for t in shlex.split(command, posix=posix)]
    except ValueError:
        return command.split()


def walk_tree(root: Path, rel_root: str = '', blacklist=()) -> list:
    """递归目录树（青龙 readDirs + dirSort）。"""
    def scan(directory: Path, prefix: str):
        nodes = []
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return nodes
        dirs, files = [], []
        for e in entries:
            if e.name in blacklist:
                continue
            if e.is_symlink():
                continue
            rel = f'{prefix}/{e.name}' if prefix else e.name
            if e.is_dir():
                dirs.append({
                    'title': e.name, 'key': rel, 'type': 'directory',
                    'parent': prefix or '/',
                    'createTime': e.stat().st_mtime * 1000,
                    'children': scan(Path(e.path), rel),
                })
            else:
                st = e.stat()
                files.append({
                    'title': e.name, 'key': rel, 'type': 'file',
                    'parent': prefix or '/',
                    'createTime': st.st_mtime * 1000,
                    'size': st.st_size,
                })
        dirs.sort(key=lambda x: x['title'])
        files.sort(key=lambda x: -x['createTime'])
        return dirs + files

    return scan(root, rel_root)


def find_bash():
    if not config.IS_WINDOWS:
        return shutil.which('bash')
    # Windows: 优先 PATH 中 bash（Git Bash）
    return shutil.which('bash')


def which_or_none(cmd: str):
    return shutil.which(cmd)


def kill_process_tree(pid: int):
    """杀掉进程及其子进程树。"""
    try:
        import psutil
    except ImportError:
        os.kill(pid, 9)
        return
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    children = proc.children(recursive=True)
    for c in children:
        try:
            c.kill()
        except psutil.Error:
            pass
    try:
        proc.kill()
    except psutil.Error:
        pass


def human_port():
    return config.PORT


def file_tail_head(path: Path, offset: int = None, limit: int = 262144, tail: bool = False):
    """青龙 logReader：按字节偏移安全读取（UTF-8 边界保护）。"""
    limit = max(4, min(int(limit or 262144), 1024 * 1024))
    if not path.exists():
        return {'content': '', 'offset': 0, 'nextOffset': 0, 'total': 0, 'truncated': False}
    total = path.stat().st_size
    if offset is None or tail:
        start = max(total - limit, 0)
    else:
        start = max(0, min(int(offset), total))
    with open(path, 'rb') as f:
        f.seek(start)
        raw = f.read(min(limit, total - start))
    # 起始处回退到 UTF-8 完整边界
    while raw and start > 0 and (raw[0] & 0xC0) == 0x80:
        raw = raw[1:]
        start += 1
    # 截去尾部不完整的 UTF-8 多字节字符，交由下次读取补齐
    cut = 0
    for i in range(1, 4):
        if len(raw) - i < 0:
            break
        b = raw[len(raw) - i]
        if (b & 0xC0) != 0x80:
            need = 1 if b < 0x80 else (2 if b < 0xE0 else (3 if b < 0xF0 else 4))
            if i < need:
                cut = i
            break
    if cut:
        raw = raw[:len(raw) - cut]
    content = raw.decode('utf-8', 'ignore')
    next_offset = start + len(raw)
    return {
        'content': content,
        'offset': start,
        'nextOffset': min(next_offset, total),
        'total': total,
        'truncated': start > 0 or next_offset < total,
    }


ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')


def remove_ansi(text: str) -> str:
    return ANSI_RE.sub('', text or '')
