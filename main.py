"""熊猫系统启动入口。

用法:
    python main.py                 # 前台运行（默认 0.0.0.0:5700）
    PD_PORT=8000 python main.py    # 换端口
"""
import os

import uvicorn

from panda import config

if __name__ == '__main__':
    uvicorn.run(
        'panda.app:create_app',
        factory=True,
        host=config.HOST,
        port=config.PORT,
        log_level='info',
        ws_ping_interval=20,
    )
