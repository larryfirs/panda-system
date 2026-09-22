"""环境变量落盘（青龙 services/env.ts set_envs）：DB 是真源，同步生成三份 preload 文件。"""
import re
from collections import OrderedDict

from . import config
from .models import ENV_NORMAL, Env

VALID_NAME = re.compile(r'^[a-zA-Z_][0-9a-zA-Z_]*$')


def group_envs(session) -> 'OrderedDict[str,str]':
    rows = (
        session.query(Env)
        .filter(Env.status == ENV_NORMAL)
        .order_by(Env.isPinned.desc(), Env.position.desc(), Env.createdAt.asc())
        .all()
    )
    grouped = OrderedDict()
    for r in rows:
        if not r.name or not VALID_NAME.match(r.name):
            continue
        grouped.setdefault(r.name, []).append(r.value or '')
    return OrderedDict((k, '&'.join(v)) for k, v in grouped.items())


def set_envs(session):
    envs = group_envs(session)

    with open(config.ENV_SH_FILE, 'w', encoding='utf-8') as f:
        f.write('#!/bin/bash\n')
        for k, v in envs.items():
            f.write("export %s='%s'\n" % (k, v.replace("'", "'\\''")))

    with open(config.ENV_JS_FILE, 'w', encoding='utf-8') as f:
        for k, v in envs.items():
            f.write('process.env.%s=`%s`;\n' % (k, v.replace('`', '\\`')))

    with open(config.ENV_PY_FILE, 'w', encoding='utf-8') as f:
        f.write('import os\n')
        for k, v in envs.items():
            f.write("os.environ['%s']='''%s'''\n" % (k, v.replace("'''", "''\\'")))

    with open(config.PRELOAD_PATH / 'sitecustomize.py', 'w', encoding='utf-8') as f:
        f.write('import runpy, pathlib\n'
                f"_p = pathlib.Path(r'{config.ENV_PY_FILE}')\n"
                'if _p.exists(): exec(compile(_p.read_text("utf-8"), str(_p), "exec"))\n')
    return envs
