from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Note: Base is now defined in app.models using SQLAlchemy 2.0 DeclarativeBase
# Import it from there when needed


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
