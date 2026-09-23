"""打包熊猫系统运行代码为 tar.gz，用于上传服务器解压部署。

用法:
    python build.py                     # 输出 dist/panda-system_v<版本>_<时间>.tar.gz
    python build.py --include-data      # 连 data/ 运行数据一起打包（默认不含）

说明:
    包内只含运行所需代码（app/ static/ samples/ main.py requirements.txt），
    顶层目录固定为 panda-system/，解压即得干净目录；
    不包含 .venv —— 服务器现场创建虚拟环境并 pip install -r requirements.txt。
"""
import argparse
import re
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJ_NAME = 'panda-system'          # 解压后的固定顶层目录名
ARCHIVE_NAME = PROJ_NAME            # 压缩包内前缀

# 仅打包这些顶层条目（--include-data 时追加 data/）
INCLUDE_TOP = ['app', 'static', 'samples', 'main.py', 'requirements.txt']
EXCLUDE_DIR_NAMES = {'__pycache__', '.venv', 'venv', 'dist', '.git', '.idea', '.vscode', 'node_modules'}


def read_version() -> str:
    init = ROOT / 'app' / '__init__.py'
    if init.is_file():
        m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", init.read_text('utf-8'))
        if m:
            return m.group(1)
    return '0.0.0'


def collect(include_data: bool):
    """产出 (绝对路径, 包内路径) 列表，包内路径统一带 panda-system/ 前缀。"""
    top_dirs = list(INCLUDE_TOP)
    if include_data:
        top_dirs.append('data')
    entries = []
    for name in top_dirs:
        target = ROOT / name
        if target.is_file():
            entries.append((target, f'{ARCHIVE_NAME}/{name}'))
            continue
        for path in sorted(target.rglob('*')):
            if path.is_dir() or path.name in {'.DS_Store', 'Thumbs.db'}:
                continue
            if any(part in EXCLUDE_DIR_NAMES for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix in {'.pyc', '.pyo'} or path.is_symlink():
                continue
            entries.append((path, f'{ARCHIVE_NAME}/{path.relative_to(ROOT).as_posix()}'))
    return entries


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    parser = argparse.ArgumentParser(description='打包熊猫系统运行代码为 tar.gz')
    parser.add_argument('--include-data', action='store_true', help='连同 data/ 运行数据一起打包')
    args = parser.parse_args()

    entries = collect(args.include_data)
    if not entries:
        print('未找到可打包的文件，请确认在项目根目录运行。', file=sys.stderr)
        sys.exit(1)

    out_dir = ROOT / 'dist'
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f'{PROJ_NAME}_v{read_version()}_{time.strftime("%Y%m%d-%H%M%S")}.tar.gz'

    with tarfile.open(out_path, 'w:gz') as tf:
        for abs_path, arc in entries:
            tf.add(abs_path, arcname=arc, recursive=False)

    size_kb = out_path.stat().st_size / 1024
    print(f'已生成: {out_path.name}')
    print(f'包含文件: {len(entries)} 个   大小: {size_kb:.1f} KB   data/ {"已含" if args.include_data else "已排除"}')
    print('\n--- 服务器部署提示 ---')
    print(f'1. 上传后解压: tar -xzf {out_path.name} -C /www/wwwroot/  →  得到 /www/wwwroot/{PROJ_NAME}/')
    print(f'2. cd /www/wwwroot/{PROJ_NAME} && python3.14 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt')
    print(f'3. 启动: ./.venv/bin/python main.py（端口读 PD_PORT，默认 3939）')


if __name__ == '__main__':
    main()
