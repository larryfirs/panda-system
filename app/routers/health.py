"""健康检查 /api/health（免鉴权）。"""
from fastapi import APIRouter

from ..core.common import ok

router = APIRouter(tags=['health'])


@router.get('/api/health')
def health():
    return ok('ok')
