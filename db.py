import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_engine = None
_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        _sync_dsn = (os.getenv("DB_DSN") or os.getenv("DATABASE_URL") or "").strip()
        if _sync_dsn:
            if _sync_dsn.startswith("postgresql://"):
                url = _sync_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif _sync_dsn.startswith("postgres://"):
                url = _sync_dsn.replace("postgres://", "postgresql+asyncpg://", 1)
            else:
                url = _sync_dsn
        else:
            # Fallback to individual components (common for local cloud-sql-proxy setup)
            user = os.getenv("DB_USER") or os.getenv("OMEGA_PG_USER") or "postgres"
            password = os.getenv("DB_PASS") or os.getenv("OMEGA_PG_PASS") or ""
            name = os.getenv("DB_NAME") or os.getenv("OMEGA_PG_DB") or "postgres"
            host = os.getenv("DB_HOST") or os.getenv("OMEGA_PG_HOST") or "localhost"
            port = os.getenv("DB_PORT") or os.getenv("OMEGA_PG_PORT") or "5432"
            url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"

        _engine = create_async_engine(
            url, 
            pool_size=10, 
            max_overflow=5, 
            pool_pre_ping=True,
            connect_args={"server_settings": {"application_name": "OmegaFastAPI"}}
        )
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_db():
    factory = _get_session_factory()
    async with factory() as session:
        yield session
