from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


engine = create_engine(
    config.DATABASE_URL,
    connect_args={'check_same_thread': False, 'timeout': 30}
    if config.DATABASE_URL.startswith('sqlite')
    else {},
)


@event.listens_for(engine, 'connect')
def _sqlite_pragma(dbapi_conn, _):
    if config.DATABASE_URL.startswith('sqlite'):
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=30000')
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def session_scope():
    return SessionLocal()
