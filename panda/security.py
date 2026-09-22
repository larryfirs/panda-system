"""密码哈希 / JWT / TOTP / 随机串，均与青龙格式兼容。"""
import hashlib
import hmac
import random
import re
import string
import time
from datetime import datetime, timedelta, timezone

import jwt
import pyotp

from . import config

PASSWORD_RE = re.compile(r'^scrypt\$[a-f0-9]{32}\$[a-f0-9]{128}$')


def hash_password(password: str) -> str:
    salt = random.SystemRandom().randbytes(16)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64,
        maxmem=128 * 1024 * 1024,
    )
    return f'scrypt${salt.hex()}${key.hex()}'


def verify_password(password: str, stored: str) -> bool:
    """校验密码；青龙式：stored 为明文时按明文比对（调用方负责升级为哈希）。"""
    if PASSWORD_RE.match(stored or ''):
        _, salt_hex, key_hex = stored.split('$')
        salt = bytes.fromhex(salt_hex)
        key = hashlib.scrypt(
            password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64,
            maxmem=128 * 1024 * 1024,
        )
        return hmac.compare_digest(key.hex(), key_hex)
    return hmac.compare_digest(password or '', stored or '')


SPECIAL_CHARS = '-_'


def create_random_string(min_len: int, max_len: int) -> str:
    length = random.randint(min_len, max_len)
    alphabet = string.ascii_lowercase + string.ascii_uppercase + string.digits + SPECIAL_CHARS
    pool = [
        random.choice(string.digits),
        random.choice(string.ascii_lowercase),
        random.choice(string.ascii_uppercase),
        random.choice(SPECIAL_CHARS),
    ]
    pool += [random.choice(alphabet) for _ in range(length - len(pool))]
    random.shuffle(pool)
    return ''.join(pool)


def make_token(data_str: str, expires_seconds: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(seconds=expires_seconds)
    payload = {'data': data_str, 'iat': int(time.time()), 'exp': exp}
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def read_token(token: str):
    return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])


def normalize_token(raw: str) -> str:
    """去掉 Bearer / mobile- / desktop- 前缀（青龙 config/util.ts getToken）。"""
    if not raw:
        return ''
    token = raw
    if token.startswith('Bearer '):
        token = token[len('Bearer '):]
    for prefix in ('mobile-', 'desktop-'):
        if token.startswith(prefix):
            token = token[len(prefix):]
    return token.strip()


def detect_platform(user_agent: str) -> str:
    ua = (user_agent or '').lower()
    if 'mobile' in ua or 'android' in ua or 'iphone' in ua:
        return 'mobile'
    return 'desktop'


def totp_verify(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name='panda')
