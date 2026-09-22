"""FastAPI 应用装配：中间件（鉴权）、路由、WebSocket、静态资源、SPA。"""
import asyncio
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config
from .init import init_data, seed_files, setup_logging, start_scheduler
from .routers import all_routers
from .security import normalize_token
from .ws import ws_manager

WHITE_LIST_EXACT = set(config.API_WHITE_LIST)


def create_app() -> FastAPI:
    setup_logging()
    config.ensure_dirs()

    app = FastAPI(title='熊猫系统 Panda', version='1.0.0', docs_url='/api/docs', openapi_url='/api/openapi.json')
    app.add_middleware(
        CORSMiddleware, allow_origins=['*'], allow_credentials=False,
        allow_methods=['*'], allow_headers=['*'],
    )

    @app.on_event('startup')
    async def _boot():
        ws_manager.set_loop(asyncio.get_running_loop())
        seed_files()
        init_data()
        start_scheduler()

    @app.middleware('http')
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        if config.BASE_URL and path.startswith(config.BASE_URL):
            path = path[len(config.BASE_URL):] or '/'
        need_auth = path.startswith('/api/') and not _is_public(path, request.method)
        if need_auth:
            token = request.headers.get('authorization', '')
            if not auth.validate_request_token(token):
                return JSONResponse({'code': 401, 'message': '身份校验失败，请重新登录'}, status_code=401)
        return await call_next(request)

    for r in all_routers:
        app.include_router(r)

    # 兼容青龙的 preload 输出
    @app.get('/api/env.js', include_in_schema=False)
    def env_js():
        if config.ENV_JS_FILE.exists():
            return PlainTextResponse(config.ENV_JS_FILE.read_text('utf-8'), media_type='application/javascript')
        return PlainTextResponse('')

    @app.get('/api/env.sh', include_in_schema=False)
    def env_sh():
        if config.ENV_SH_FILE.exists():
            return PlainTextResponse(config.ENV_SH_FILE.read_text('utf-8'))
        return PlainTextResponse('')

    @app.get('/api/env.py', include_in_schema=False)
    def env_py():
        if config.ENV_PY_FILE.exists():
            return PlainTextResponse(config.ENV_PY_FILE.read_text('utf-8'))
        return PlainTextResponse('')

    # 上传的头像等静态文件
    app.mount('/api/static', StaticFiles(directory=str(config.UPLOAD_PATH)), name='upload')
    app.mount('/assets', StaticFiles(directory=str(config.STATIC_DIR / 'assets')), name='assets')

    @app.websocket('/api/ws')
    async def ws_endpoint(ws: WebSocket):
        token = normalize_token(ws.query_params.get('token', ''))
        if not auth.validate_request_token(f'Bearer {token}'):
            await ws.close(code=404)
            return
        await ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()  # 心跳/忽略
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    @app.get('/', include_in_schema=False)
    @app.get('/index.html', include_in_schema=False)
    def spa():
        return FileResponse(config.STATIC_DIR / 'index.html')

    @app.exception_handler(Exception)
    async def _err(request: Request, exc: Exception):
        import logging
        logging.getLogger('panda').exception('请求处理异常')
        return JSONResponse({'code': 500, 'message': str(exc)}, status_code=500)

    return app


def _is_public(path: str, method: str) -> bool:
    if path.startswith('/api/static') or path in ('/api/docs', '/api/openapi.json'):
        return True
    if path in WHITE_LIST_EXACT:
        return True
    # GET /api/system 免鉴权（初始化检测）
    if path.rstrip('/') == '/api/system' and method == 'GET':
        return True
    return False
