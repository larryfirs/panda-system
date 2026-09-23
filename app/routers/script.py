"""脚本管理 /api/scripts —— 文件树/读写/上传/重命名/删除/下载/在线运行（WebSocket 输出）。"""
import shutil
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse

from .. import utils
from ..core import config
from ..services import executor
from ..core.common import fail, ok
from ..services.ws import ws_manager

router = APIRouter(prefix='/api/scripts', tags=['script'])

_swap_procs: dict = {}  # filename -> Popen


def _resolve(path: str, filename: str = ''):
    return utils.resolve_file_access(
        config.SCRIPT_PATH, [p for p in (path, filename) if p], config.BLACK_DIR_LIST + config.BLACK_FILE_LIST
    )


@router.get('')
def list_scripts(path: str = Query('')):
    if path:
        try:
            target = _resolve(path)
        except PermissionError:
            return fail('暂无权限', 403)
        nodes = []
        dirs, files = [], []
        for e in sorted(target.iterdir(), key=lambda x: x.name):
            if e.name in config.BLACK_DIR_LIST:
                continue
            rel = f'{path.rstrip("/")}/{e.name}'
            if e.is_dir():
                dirs.append({'title': e.name, 'key': rel, 'type': 'directory', 'parent': path or '/',
                             'createTime': e.stat().st_mtime * 1000, 'children': []})
            else:
                files.append({'title': e.name, 'key': rel, 'type': 'file', 'parent': path or '/',
                              'createTime': e.stat().st_mtime * 1000, 'size': e.stat().st_size})
        return ok({'title': Path(path).name or 'scripts', 'key': path or '/', 'type': 'directory',
                   'parent': '/', 'dir': True, 'children': dirs + files})
    tree = utils.walk_tree(config.SCRIPT_PATH, '', config.BLACK_DIR_LIST)
    return ok({'title': 'scripts', 'key': '/', 'type': 'directory', 'parent': '/', 'dir': True, 'children': tree})


@router.get('/detail')
def read_script(path: str = Query(''), file: str = Query('')):
    try:
        fp = _resolve(path, file)
    except PermissionError:
        return fail('暂无权限', 403)
    if not fp.is_file():
        return fail('文件不存在', 404)
    return ok(fp.read_text('utf-8', errors='replace'))


@router.post('')
async def create_script(
    request: Request,
    file: UploadFile = File(None),
    filename: str = Form(''),
    path: str = Form(''),
    content: str = Form(''),
    directory: str = Form(''),
    originFilename: str = Form(''),
):
    filename = filename or (file.filename if file else '')
    if not filename and not directory:
        return fail('缺少文件名', 400)
    try:
        if directory:
            target = _resolve(directory)
            target.mkdir(parents=True, exist_ok=True)
            return ok('新建文件夹成功')
        target = _resolve(path, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():  # 同名先备份（青龙 data/bak）
            shutil.copy2(target, config.BAK_PATH / f'{int(time.time() * 1000)}_{target.name}')
        if file:
            data = await file.read()
            target.write_bytes(data)
        else:
            target.write_text(content or '', 'utf-8')
        # 移动/重命名语义：带 originFilename 时删除原文件
        if originFilename:
            try:
                old = _resolve(path, originFilename)
                if old != target and old.exists():
                    if old.is_dir():
                        shutil.rmtree(old)
                    else:
                        old.unlink()
            except PermissionError:
                pass
        return ok('保存成功')
    except PermissionError:
        return fail('暂无权限', 403)


@router.put('')
def save_script(payload: dict = Body(...)):
    filename = payload.get('filename') or ''
    try:
        target = _resolve(payload.get('path', ''), filename)
    except PermissionError:
        return fail('暂无权限', 403)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload.get('content') or '', 'utf-8')
    return ok('保存成功')


@router.put('/rename')
def rename_script(payload: dict = Body(...)):
    try:
        old = _resolve(payload.get('path', ''), payload.get('filename') or '')
        new = old.parent / (payload.get('newFilename') or '')
        if new.name != (payload.get('newFilename') or ''):
            return fail('新文件名不合法', 400)
    except PermissionError:
        return fail('暂无权限', 403)
    if not old.exists() or new.exists():
        return fail('文件名冲突或原文件不存在', 400)
    old.rename(new)
    return ok('重命名成功')


@router.delete('')
def delete_script(payload: dict = Body(...)):
    try:
        target = _resolve(payload.get('path', ''), payload.get('filename') or '')
    except PermissionError:
        return fail('暂无权限', 403)
    if not target.exists():
        return fail('文件不存在', 404)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return ok('删除成功')


@router.post('/download')
def download_script(payload: dict = Body(...)):
    try:
        target = _resolve(payload.get('path', ''), payload.get('filename') or '')
    except PermissionError:
        return fail('暂无权限', 403)
    if not target.is_file():
        return fail('文件不存在', 404)
    return FileResponse(target, filename=payload.get('filename') or target.name)


@router.put('/run')
def run_script(payload: dict = Body(...)):
    """临时 swap 文件执行，输出经 WebSocket 推送（青龙 manuallyRunScript 协议）。"""
    filename = payload.get('filename') or ''
    stem = Path(filename).stem
    content = payload.get('content')
    try:
        target = _resolve(payload.get('path', ''), filename)
    except PermissionError:
        return fail('暂无权限', 403)
    swap = target.parent / f'.{stem}.swap{target.suffix}'
    swap.write_text(content if content is not None else (target.read_text('utf-8', errors='replace') if target.exists() else ''), 'utf-8')

    try:
        args = payload.get('args') or []
        argv = _argv_for(swap, args)
        proc = subprocess.Popen(
            argv, cwd=str(config.SCRIPT_PATH),
            env=executor_build_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    except Exception as e:
        swap.unlink(missing_ok=True)
        return fail(f'启动失败: {e}', 500)
    _swap_procs[filename] = proc

    def pump():
        for line in iter(proc.stdout.readline, b''):
            ws_manager.broadcast('manuallyRunScript', line.decode('utf-8', 'replace'), references=[filename])
        code = proc.wait()
        ws_manager.broadcast('manuallyRunScript', f'\n进程已退出，退出码 {code}', references=[filename], status='success')
        swap.unlink(missing_ok=True)
        _swap_procs.pop(filename, None)

    import threading
    threading.Thread(target=pump, daemon=True).start()
    return ok(proc.pid)


@router.put('/stop')
def stop_script(payload: dict = Body(...)):
    filename = payload.get('filename') or ''
    pid = payload.get('pid')
    proc = _swap_procs.pop(filename, None)
    if proc:
        utils.kill_process_tree(proc.pid)
    elif pid:
        utils.kill_process_tree(int(pid))
    return ok('停止成功')


def _argv_for(path: Path, args):
    ext = path.suffix.lower()
    if ext in ('.py', '.pyc'):
        return [config.PYTHON_BIN, str(path)] + args
    if ext in ('.js', '.mjs', '.cjs'):
        return [utils.which_or_none('node') or 'node', str(path)] + args
    if ext == '.ts':
        tsx = utils.which_or_none('tsx')
        return [tsx or 'tsx', str(path)] + args
    if ext == '.sh':
        bash = utils.find_bash()
        if not bash:
            raise RuntimeError('未找到 bash')
        return [bash, str(path)] + args
    if ext == '.ps1':
        return [utils.which_or_none('powershell') or 'powershell', '-File', str(path)] + args
    return [str(path)] + args


def executor_build_env():
    from ..core.db import SessionLocal
    with SessionLocal() as s:
        return executor.build_env(s)
