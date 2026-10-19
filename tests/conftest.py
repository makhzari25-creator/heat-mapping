"""
Test configuration — creates a fresh in-memory SQLite DB per test session.
Uses pytest-asyncio with auto mode so all async tests run without decoration issues.
"""
import os
import sys
import pytest
import pytest_asyncio

# Add app to path BEFORE any other imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

# Use OS-compatible temp directory
import tempfile
from pathlib import Path

temp_dir = tempfile.gettempdir()
db_file = Path(temp_dir) / "test_store_intelligence.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{db_file.absolute().as_posix()}"
os.environ["DATABASE_URL"] = TEST_DB_URL

# Now import after env is set
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text


@pytest.fixture(scope="session", autouse=True)
def event_loop_policy():
    """Use default asyncio event loop."""
    import asyncio
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    """Create DB tables once for the entire test session."""
    # Remove stale DB
    import os as _os
    db_path = db_file.absolute().as_posix()
    if _os.path.exists(db_path):
        try:
            _os.unlink(db_path)
        except Exception:
            pass

    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                store_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                visitor_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                zone_id TEXT,
                dwell_ms INTEGER DEFAULT 0,
                is_staff INTEGER DEFAULT 0,
                confidence REAL DEFAULT 1.0,
                metadata TEXT DEFAULT '{}'
            )
        """))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user_role TEXT NOT NULL,
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_store_date ON events(store_id, timestamp)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_event_type ON events(store_id, event_type, timestamp)"
        ))
    await engine.dispose()

    # Patch the database module to use our test engine
    import database as db_module
    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    test_session = async_sessionmaker(test_engine, expire_on_commit=False)
    db_module.engine = test_engine
    db_module.AsyncSessionLocal = test_session

    yield

    await test_engine.dispose()
