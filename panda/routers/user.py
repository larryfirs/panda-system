"""用户管理：登录/登出/初始化/改密/2FA/登录日志/IP 黑名单/通知设置/头像。"""
import math
import time
from pathlib import Path

from fastapi import APIRouter, Body, File, Request, UploadFile

from .. import auth, config, notify, security, utils
from ..common import fail, msg, ok
from ..db import SessionLocal

router = APIRouter(prefix='/api/user', tags=['user'])

_login_attempts: dict = {}  # ip -> [(ts)] 速率限制：15min/100
_RATE_WINDOW = 15 * 60
_RATE_MAX = 100


def _rate_limit(ip: str):
    now = time.time()
    lst = [t for t in _login_attempts.get(ip, []) if now - t < _RATE_WINDOW]
    if len(lst) >= _RATE_MAX:
        return False
    lst.append(now)
    _login_attempts[ip] = lst
    return True


def _check_lock(info: dict):
    retries = info.get('retries') or 0
    if retries > 2:
        last = info.get('lastlogon') or 0
        wait = int(3 ** retries - (time.time() * 1000 - last) / 1000)
        if wait > 0:
            return wait
    return 0


def _do_login(payload, request: Request):
    ip = auth.client_ip(request)
    if not _rate_limit(ip):
        return fail('登录尝试过于频繁，请稍后再试', 429)
    info = auth.get_auth_info()
    if ip in (info.get('blockedIps') or []):
        return fail('该 IP 已被列入黑名单', 403)
    if auth.is_default_auth(info):
        return fail('请先初始化账号', 450)

    wait = _check_lock(info)
    if wait:
        return _locked(wait)

    username = (payload.get('username') or '').strip()
    password = payload.get('password') or ''

    if not security.verify_password(password, info.get('password', '')) or username != info.get('username'):
        info['retries'] = (info.get('retries') or 0) + 1
        info['lastlogon'] = int(time.time() * 1000)
        auth.save_auth_info(info)
        auth.record_login_log(ip, 1)
        return fail('用户名或密码错误', 400)

    # 明文密码自动升级为 scrypt 哈希（青龙迁移逻辑）
    if not security.PASSWORD_RE.match(info.get('password', '')):
        info['password'] = security.hash_password(password)

    platform = security.detect_platform(request.headers.get('user-agent', ''))

    if info.get('twoFactorActivated'):
        info['isTwoFactorChecking'] = True
        info['twoFactorExpiresAt'] = int(time.time() * 1000) + 5 * 60 * 1000
        auth.save_auth_info(info)
        return {'code': 420, 'message': '需要两步验证'}

    return _issue_token(info, platform, ip, request)


def _locked(wait: int):
    from fastapi.responses import JSONResponse
    return JSONResponse({'code': 410, 'data': wait, 'message': '尝试次数过多'})


def _issue_token(info, platform, ip, request):
    token = security.make_token(
        security.create_random_string(50, 100),
        config.jwt_expires_seconds(bool(info.get('twoFactorActivated'))),
    )
    auth.add_token(info, token, platform, ip)
    auth.save_auth_info(info)
    auth.record_login_log(ip, 0, platform)
    try:
        notify.send_notify('熊猫系统登录提醒', f'时间: {time.strftime("%Y-%m-%d %H:%M:%S")}\nIP: {ip}')
    except Exception:
        pass
    return ok({
        'token': token,
        'lastip': info.get('lastip'),
        'lastaddr': info.get('lastaddr'),
        'lastlogon': info.get('lastlogon'),
        'retries': 0,
        'platform': platform,
    })


@router.post('/login')
def login(request: Request, payload: dict = Body(default={})):
    return _do_login(payload, request)


@router.put('/two-factor/login')
def two_factor_login(request: Request, payload: dict = Body(default={})):
    ip = auth.client_ip(request)
    info = auth.get_auth_info()
    now = int(time.time() * 1000)
    if not info.get('twoFactorActivated'):
        return fail('未开启两步验证', 450)
    if not info.get('isTwoFactorChecking') or (info.get('twoFactorExpiresAt') or 0) < now:
        return fail('两步验证会话已过期，请重新登录', 450)
    if not _rate_limit(ip):
        return fail('尝试过于频繁', 429)
    code = str(payload.get('code') or '')
    if info.get('lastTwoFactorStep') and now - info['lastTwoFactorStep'] < 30000 and code == info.get('_lastCode'):
        return fail('验证码 30 秒内不可重用', 430)
    if not security.totp_verify(info.get('twoFactorSecret', ''), code):
        fails = (info.get('twoFactorFails') or 0) + 1
        info['twoFactorFails'] = fails
        if fails >= 5:
            info['isTwoFactorChecking'] = False
            info['twoFactorFails'] = 0
        auth.save_auth_info(info)
        return fail('验证失败', 430)
    info['isTwoFactorChecking'] = False
    info['twoFactorFails'] = 0
    info['lastTwoFactorStep'] = now
    platform = security.detect_platform(request.headers.get('user-agent', ''))
    return _issue_token(info, platform, ip, request)


@router.put('/init')
def initialize(request: Request, payload: dict = Body(default={})):
    if not auth.is_default_auth(auth.get_auth_info()):
        return fail('账号已初始化', 450)
    username = (payload.get('username') or '').strip()
    password = payload.get('password') or ''
    if not username or not password:
        return fail('用户名与密码不能为空', 400)
    if password == config.DEFAULT_PASSWORD:
        return fail('密码不能为默认值', 400)
    info = auth.get_auth_info()
    info['username'] = username
    info['password'] = security.hash_password(password)
    auth.save_auth_info(info)
    return msg('初始化成功')


@router.get('')
@router.get('/')
def user_info():
    info = auth.get_auth_info()
    return ok({
        'username': info.get('username'),
        'avatar': info.get('avatar') or '',
        'twoFactorActivated': bool(info.get('twoFactorActivated')),
    })


@router.put('')
@router.put('/')
def update_account(payload: dict = Body(default={})):
    username = (payload.get('username') or '').strip()
    password = payload.get('password') or ''
    if password == config.DEFAULT_PASSWORD:
        return fail('密码不能为默认值', 400)
    info = auth.get_auth_info()
    if username:
        info['username'] = username
    if password:
        info['password'] = security.hash_password(password)
        # 改密踢出全部会话
        info['token'] = None
        info['tokens'] = {'desktop': [], 'mobile': []}
    auth.save_auth_info(info)
    return ok(info.get('token'))


@router.post('/logout')
def logout(request: Request):
    token = security.normalize_token(request.headers.get('authorization', ''))
    info = auth.get_auth_info()
    auth.remove_token(info, token)
    auth.save_auth_info(info)
    return msg('已退出')


@router.get('/login-log')
def login_log():
    with SessionLocal() as s:
        row = s.query(auth.SystemRow).filter(auth.SystemRow.type == 'loginLog').first()
        return ok((row.info if row else []) or [])


@router.get('/ip-blacklist')
def get_blacklist():
    info = auth.get_auth_info()
    return ok(info.get('blockedIps') or [])


@router.put('/ip-blacklist')
def add_blacklist(payload: dict = Body(default={})):
    ip = (payload.get('ip') or '').strip()
    if not _valid_ip(ip):
        return fail('IP 格式不合法', 400)
    info = auth.get_auth_info()
    lst = info.setdefault('blockedIps', [])
    if ip not in lst:
        lst.append(ip)
    auth.save_auth_info(info)
    return ok(lst)


@router.delete('/ip-blacklist')
def del_blacklist(payload: dict = Body(default={})):
    ip = (payload.get('ip') or '').strip()
    info = auth.get_auth_info()
    lst = info.setdefault('blockedIps', [])
    if ip in lst:
        lst.remove(ip)
    auth.save_auth_info(info)
    return ok(lst)


def _valid_ip(ip: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


# ---------- 两步验证管理 ----------

@router.get('/two-factor/init')
def tf_init():
    info = auth.get_auth_info()
    if info.get('twoFactorActivated'):
        return fail('已开启两步验证', 400)
    secret = security.totp_secret()
    info['pendingTwoFactorSecret'] = secret
    auth.save_auth_info(info)
    return ok({'secret': secret, 'qrMessage': security.totp_uri(secret, info.get('username', 'admin'))})


@router.put('/two-factor/active')
def tf_active(payload: dict = Body(default={})):
    info = auth.get_auth_info()
    secret = info.get('pendingTwoFactorSecret')
    if not secret:
        return fail('请先生成密钥', 400)
    if not security.totp_verify(secret, str(payload.get('code') or '')):
        return fail('验证码错误', 430)
    info['twoFactorSecret'] = secret
    info['pendingTwoFactorSecret'] = None
    info['twoFactorActivated'] = True
    info['token'] = None
    info['tokens'] = {'desktop': [], 'mobile': []}
    auth.save_auth_info(info)
    return msg('两步验证已开启，请重新登录')


@router.put('/two-factor/deactivate')
def tf_deactivate():
    info = auth.get_auth_info()
    info['twoFactorActivated'] = False
    info['twoFactorSecret'] = ''
    auth.save_auth_info(info)
    return msg('两步验证已关闭')


# ---------- 通知设置 ----------

@router.get('/notification')
def get_notification():
    return ok(auth.get_notification_info())


@router.put('/notification')
def put_notification(payload: dict = Body(default={})):
    auth.put_notification_info(payload)
    sent = notify.send_notify('熊猫系统测试通知', '这是一条来自熊猫系统的测试通知')
    if payload.get('type') and not sent:
        return fail('通知发送失败，请检查配置', 400)
    return ok(payload)


@router.put('/notification/init')
def put_notification_init():
    auth.put_notification_info({})
    return msg('已重置通知设置')


# ---------- 头像 ----------

@router.put('/avatar')
async def upload_avatar(file: UploadFile = File(...)):
    ext = Path(file.filename or 'avatar.png').suffix.lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif'):
        return fail('不支持的图片格式', 400)
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        return fail('图片不能超过 5MB', 400)
    name = f'avatar_{int(time.time() * 1000)}{ext}'
    (config.UPLOAD_PATH / name).write_bytes(data)
    info = auth.get_auth_info()
    old = info.get('avatar') or ''
    info['avatar'] = name
    auth.save_auth_info(info)
    if old:
        (config.UPLOAD_PATH / Path(old).name).unlink(missing_ok=True)
    return ok(name)
