from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text

import models
from config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=True
)

def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _apply_sqlite_migrations()
    
def get_session():
    with Session(engine) as session:
        yield session


def _apply_sqlite_migrations() -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as conn:
        table_exists = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='reports'"
            )
        ).first()
        if not table_exists:
            return

        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(reports)")).fetchall()
        }

        if "template_id" not in columns:
            conn.execute(text("ALTER TABLE reports ADD COLUMN template_id INTEGER"))
        if "template_key" not in columns:
            conn.execute(text("ALTER TABLE reports ADD COLUMN template_key TEXT"))
        if "template_version" not in columns:
            conn.execute(text("ALTER TABLE reports ADD COLUMN template_version INTEGER"))
