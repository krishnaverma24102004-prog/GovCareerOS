import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from govcareeros.backend.config.settings import settings
from govcareeros.backend.models import Base

FIXTURES = Path(__file__).parent / "fixtures"

def _postgres_reachable(url: str) -> bool:
    async def probe():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("select 1"))
            return True
        except Exception:
            return False
        finally:
            await engine.dispose()
    return asyncio.run(probe())

_PG_URL = settings.TEST_DATABASE_URL
_PG_OK = bool(_PG_URL) and _postgres_reachable(_PG_URL)

@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def db_session(request, tmp_path, monkeypatch):
    """A fresh schema per test, on SQLite (always) and PostgreSQL (when TEST_DATABASE_URL is reachable)."""
    if request.param == "postgres":
        if not _PG_OK:
            pytest.skip("TEST_DATABASE_URL not set or PostgreSQL unreachable")
        engine = create_async_engine(_PG_URL)
    else:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    # Raw documents are written to disk; keep them out of the real storage dir
    monkeypatch.setattr(settings, "STORAGE_PATH", str(tmp_path / "storage"))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)()
    try:
        yield session
    finally:
        await session.close()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

@pytest_asyncio.fixture
async def api(db_session):
    """httpx client against the FastAPI app, sharing the per-test database session."""
    import httpx
    from govcareeros.backend.api.deps import get_session
    from govcareeros.backend.api.main import create_app

    app = create_app()

    async def _session():
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_session] = _session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client

@pytest.fixture
def login(api):
    """Register + log in a user; returns the Authorization header."""
    async def _login(email, password="correct-horse-battery"):
        r = await api.post("/auth/register", json={"email": email, "password": password, "full_name": email.split("@")[0]})
        assert r.status_code == 201, r.text
        r = await api.post("/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _login
