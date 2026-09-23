"""数据模型：与青龙面板 Sequelize 模型逐字段对齐（表名/列名保持一致）。"""
import time
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..core.db import Base

# 任务状态（back/data/cron.ts）
CRON_RUNNING = 0
CRON_IDLE = 1
CRON_DISABLED = 2
CRON_QUEUED = 3

# 实例状态（back/data/runningInstance.ts）
INSTANCE_RUNNING = 0
INSTANCE_FINISHED = 1
INSTANCE_STOPPED = 2
INSTANCE_ERROR = 3

# 依赖类型（back/data/dependence.ts）
DEP_NODEJS = 0
DEP_PYTHON3 = 1
DEP_LINUX = 2
DEP_TYPE_NAMES = {DEP_NODEJS: 'nodejs', DEP_PYTHON3: 'python3', DEP_LINUX: 'linux'}

# 依赖状态
DEP_INSTALLING = 0
DEP_INSTALLED = 1
DEP_INSTALL_FAILED = 2
DEP_REMOVING = 3
DEP_REMOVED = 4
DEP_REMOVE_FAILED = 5
DEP_QUEUED = 6
DEP_CANCELLED = 7

ENV_NORMAL = 0
ENV_DISABLED = 1

# Env 排序常量（back/data/env.ts）
MAX_POSITION = 9e15
INIT_POSITION = 4.5e15
STEP_POSITION = 1e10
MIN_POSITION = 100

VIEW_SYSTEM = 1
VIEW_PERSONAL = 2


def _now() -> datetime:
    return datetime.now()


class TimestampMixin:
    createdAt: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updatedAt: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Crontab(Base, TimestampMixin):
    __tablename__ = 'crontabs'
    __table_args__ = (UniqueConstraint('name', 'command', 'schedule', name='composite_index'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), default='')
    command: Mapped[str] = mapped_column(String(255))
    schedule: Mapped[str] = mapped_column(String(255), default='')
    timestamp: Mapped[str] = mapped_column(String(255), default=lambda: str(datetime.now()))
    saved: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=CRON_IDLE)
    isSystem: Mapped[int] = mapped_column(Integer, default=0)
    pid: Mapped[int] = mapped_column(Integer, nullable=True)
    isDisabled: Mapped[int] = mapped_column(Integer, default=0)
    isPinned: Mapped[int] = mapped_column(Integer, default=0)
    log_path: Mapped[str] = mapped_column(String(255), default='')
    queued_token: Mapped[str] = mapped_column(String(255), nullable=True)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    last_running_time: Mapped[int] = mapped_column(Integer, default=0)
    last_execution_time: Mapped[int] = mapped_column(Integer, default=0)
    sub_id: Mapped[int] = mapped_column(Integer, nullable=True)
    extra_schedules: Mapped[list] = mapped_column(JSON, nullable=True)
    task_before: Mapped[str] = mapped_column(Text, nullable=True)
    task_after: Mapped[str] = mapped_column(Text, nullable=True)
    log_name: Mapped[str] = mapped_column(String(255), nullable=True)
    allow_multiple_instances: Mapped[int] = mapped_column(Integer, default=0)
    work_dir: Mapped[str] = mapped_column(String(255), nullable=True)

    def is_active(self) -> bool:
        return not self.isDisabled and self.status != CRON_DISABLED


class CronView(Base, TimestampMixin):
    __tablename__ = 'cronviews'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    position: Mapped[float] = mapped_column(Float, default=0)
    isDisabled: Mapped[int] = mapped_column(Integer, default=0)
    filters: Mapped[list] = mapped_column(JSON, nullable=True)
    sorts: Mapped[list] = mapped_column(JSON, nullable=True)
    filterRelation: Mapped[str] = mapped_column(String(16), nullable=True)
    type: Mapped[int] = mapped_column(Integer, default=VIEW_PERSONAL)


class CronStat(Base, TimestampMixin):
    __tablename__ = 'cronstats'
    __table_args__ = (UniqueConstraint('ref_id', 'date', name='uq_ref_date'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref_id: Mapped[int] = mapped_column(Integer, index=True)
    date: Mapped[str] = mapped_column(String(16), index=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    total_time: Mapped[int] = mapped_column(Integer, default=0)  # 毫秒
    max_time: Mapped[int] = mapped_column(Integer, default=0)


class Dependence(Base, TimestampMixin):
    __tablename__ = 'dependences'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[int] = mapped_column(Integer, default=DEP_NODEJS)
    timestamp: Mapped[str] = mapped_column(String(255), default=lambda: str(datetime.now()))
    status: Mapped[int] = mapped_column(Integer, default=DEP_QUEUED)
    log: Mapped[list] = mapped_column(JSON, default=list)
    remark: Mapped[str] = mapped_column(String(255), default='')


class Env(Base, TimestampMixin):
    __tablename__ = 'envs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    value: Mapped[str] = mapped_column(String(255))
    timestamp: Mapped[str] = mapped_column(String(255), default=lambda: str(datetime.now()))
    status: Mapped[int] = mapped_column(Integer, default=ENV_NORMAL)
    position: Mapped[float] = mapped_column(Float, default=INIT_POSITION)
    name: Mapped[str] = mapped_column(String(255))
    remarks: Mapped[str] = mapped_column(String(255), default='')
    isPinned: Mapped[int] = mapped_column(Integer, default=0)
    labels: Mapped[list] = mapped_column(JSON, default=list)


class SystemRow(Base, TimestampMixin):
    """青龙 Auths 表：type ∈ systemConfig/notification/authConfig/loginLog."""

    __tablename__ = 'auths'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    info: Mapped[dict] = mapped_column(JSON, nullable=True)


class RunningInstance(Base, TimestampMixin):
    __tablename__ = 'runninginstances'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cron_id: Mapped[int] = mapped_column(Integer, index=True)
    pid: Mapped[int] = mapped_column(Integer, nullable=True)
    log_path: Mapped[str] = mapped_column(String(255), nullable=True)
    started_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))
    finished_at: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[int] = mapped_column(Integer, default=INSTANCE_RUNNING)
    exit_code: Mapped[int] = mapped_column(Integer, nullable=True)
