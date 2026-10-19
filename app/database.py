from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text, event
from config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"timeout": 30}
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=OFF")
    cursor.execute("PRAGMA cache_size=-64000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
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
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_visitor ON events(store_id, visitor_id)"
        ))
