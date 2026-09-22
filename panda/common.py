"""路由公共工具：统一响应体 {code, data?, message?}（与青龙一致）。"""
from fastapi import Request
from fastapi.responses import JSONResponse


def ok(data=None, code=200):
    return {'code': code, 'data': data}


def msg(message: str, code=200):
    return {'code': code, 'message': message}


def fail(message: str, code=400, http_status=200):
    return JSONResponse({'code': code, 'message': message}, status_code=http_status)


def body_int_ids(payload) -> list:
    if isinstance(payload, list):
        return [int(x) for x in payload]
    return []


def query_int(q: str, default=None):
    try:
        return int(q)
    except (TypeError, ValueError):
        return default
