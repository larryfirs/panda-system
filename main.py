"""熊猫系统启动入口（Windows / FastAPI + uvicorn）。

用法:
    python main.py                 # 前台运行（默认 0.0.0.0:3939）
    PD_PORT=8000 python main.py    # 换端口
"""
import time

import uvicorn

from app.core import config
from app import __version__

if __name__ == '__main__':
    sep = '=' * 60
    print(f'\n{sep}\n[Panda] v{__version__} 启动于 {time.strftime("%Y-%m-%d %H:%M:%S")} '
          f'监听 {config.HOST}:{config.PORT}\n{sep}', flush=True)
    uvicorn.run(
        'app.main:create_app',
        factory=True,
        host=config.HOST,
        port=config.PORT,
        log_level='info',
        ws_ping_interval=20,
    )
