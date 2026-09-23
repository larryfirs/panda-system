"""会话与凭据管理（青龙 services/user.ts + shared/auth.ts）。

单管理员账号体系：凭据存 SystemRow(type=authConfig)，token 列表按平台分组，
服务端持有 token 黑名单语义（登出/改密即失效）；system token 供任务进程回调。
"""
import json
import threading
import time

from ..core import config, security
from ..core.db import SessionLocal
from ..models import SystemRow

_auth_lock = threading.Lock()

SYSTEM_CONFIG_DEFAULTS = {
    'lang': 'zh-CN',
    'panelTitle': '熊猫系统',
    'logRemoveFrequency': None,
    'cronConcurrency': None,
    'dependenceProxy': '',
    'nodeMirror': '',
    'pythonMirror': '',
    'linuxMirror': '',
    'timezone': config.DEFAULT_TIMEZONE,
    'commandTimeout': 0,
    'runningInstanceRetentionDays': None,
    'cronStatRetentionDays': None,
}

AUTH_DEFAULTS = {
    'username': config.DEFAULT_USERNAME,
    'password': config.DEFAULT_PASSWORD,
    'retries': 0,
    'lastlogon': None,
    'lastip': '',
    'lastaddr': '',
    'platform': 'desktop',
    'isTwoFactorChecking': False,
    'twoFactorExpiresAt': None,
    'lastTwoFactorStep': None,
    'token': None,
    'tokens': {'desktop': [], 'mobile': []},
    'twoFactorActivated': False,
    'twoFactorSecret': '',
    'avatar': '',
    'blockedIps': [],
}


def _get_or_create(session, type_: str, info_default) -> SystemRow:
    row = session.query(SystemRow).filter(SystemRow.type == type_).first()
    if not row:
        row = SystemRow(type=type_, info=dict(info_default) if callable(info_default) else info_default)
        session.add(row)
        session.commit()
    return row


def get_auth_info(session=None) -> dict:
    own = session is None
    s = session or SessionLocal()
    try:
        row = s.query(SystemRow).filter(SystemRow.type == 'authConfig').first()
        if not row:
            row = SystemRow(type='authConfig', info=dict(AUTH_DEFAULTS))
            s.add(row)
            s.commit()
        info = {**AUTH_DEFAULTS, **(row.info or {})}
        info['_row'] = row
        return info
    finally:
        if own:
            s.close()


def save_auth_info(info: dict, session=None):
    own = session is None
    s = session or SessionLocal()
    with _auth_lock:
        row = s.query(SystemRow).filter(SystemRow.type == 'authConfig').first()
        data = {k: v for k, v in info.items() if not k.startswith('_')}
        if row is None:
            row = SystemRow(type='authConfig', info=data)
            s.add(row)
        else:
            row.info = data
        s.commit()
    if own:
        s.close()


def is_default_auth(info: dict) -> bool:
    return info.get('username') == config.DEFAULT_USERNAME and info.get('password') == config.DEFAULT_PASSWORD


def is_initialized() -> bool:
    return not is_default_auth(get_auth_info())


def get_system_config_row() -> SystemRow:
    with SessionLocal() as s:
        return _get_or_create(s, 'systemConfig', {**SYSTEM_CONFIG_DEFAULTS})


def get_system_config_info() -> dict:
    return {**SYSTEM_CONFIG_DEFAULTS, **(get_system_config_row().info or {})}


def put_system_config_info(patch: dict):
    with SessionLocal() as s:
        row = _get_or_create(s, 'systemConfig', {})
        row.info = {**SYSTEM_CONFIG_DEFAULTS, **(row.info or {}), **patch}
        s.commit()
        return row.info


def get_notification_info() -> dict:
    with SessionLocal() as s:
        row = _get_or_create(s, 'notification', {})
        return dict(row.info or {})


def put_notification_info(info: dict):
    with SessionLocal() as s:
        row = _get_or_create(s, 'notification', {})
        row.info = info
        s.commit()


def add_token(info: dict, token: str, platform: str, ip: str = '', address: str = ''):
    tokens = info.setdefault('tokens', {'desktop': [], 'mobile': []})
    lst = tokens.setdefault(platform, [])
    lst.append(token)
    if len(lst) > config.MAX_TOKENS_PER_PLATFORM:
        tokens[platform] = lst[-config.MAX_TOKENS_PER_PLATFORM:]
    info['token'] = token
    info['platform'] = platform
    info['retries'] = 0
    info['lastlogon'] = int(time.time() * 1000)
    info['lastip'] = ip
    info['lastaddr'] = address


def remove_token(info: dict, token: str):
    if info.get('token') == token:
        info['token'] = None
    for lst in (info.get('tokens') or {}).values():
        if token in lst:
            lst.remove(token)


def all_valid_tokens(info: dict):
    out = set()
    for lst in (info.get('tokens') or {}).values():
        out.update(lst)
    if info.get('token'):
        out.add(info['token'])
    return out


# ---------- system token（供任务脚本/外部回调，写 data/config/token.json） ----------

def get_system_token() -> dict:
    if config.TOKEN_FILE.exists():
        try:
            return json.loads(config.TOKEN_FILE.read_text('utf-8'))
        except Exception:
            pass
    value = security.create_random_string(32, 32)
    doc = {'value': value, 'expiration': int(time.time()) + 28 * 86400}
    config.TOKEN_FILE.write_text(json.dumps(doc), 'utf-8')
    return doc


def check_system_token(token: str) -> bool:
    doc = get_system_token()
    return doc.get('value') == token and doc.get('expiration', 0) > time.time()


def validate_request_token(token: str) -> bool:
    """JWT 有效 且 仍在服务端会话列表中（或为 system token）。"""
    token = security.normalize_token(token)
    if not token:
        return False
    try:
        security.read_token(token)
    except Exception:
        return False
    if check_system_token(token):
        return True
    info = get_auth_info()
    return token in all_valid_tokens(info)


def record_login_log(ip: str, status: int, platform: str = 'desktop', address: str = ''):
    with SessionLocal() as s:
        row = _get_or_create(s, 'loginLog', [])
        logs = list(row.info or [])
        logs.insert(0, {
            'timestamp': int(time.time() * 1000),
            'address': address,
            'ip': ip,
            'platform': platform,
            'status': status,  # 0 成功 / 1 失败
        })
        row.info = logs[:100]
        s.commit()


def client_ip(request) -> str:
    xff = request.headers.get('x-forwarded-for')
    if xff:
        return xff.split(',')[0].strip()
    return request.client.host if request.client else ''
