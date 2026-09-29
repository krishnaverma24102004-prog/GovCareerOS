from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from govcareeros.backend.config.settings import settings

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DB_ECHO)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session