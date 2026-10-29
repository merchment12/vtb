"""Подключение к БД: engine, фабрика сессий и базовый класс таблиц."""

import hashlib
import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

log = logging.getLogger(__name__)

IS_SQLITE = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if IS_SQLITE else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

SCHEMA_TABLE = "_schema_fingerprint"


class Base(DeclarativeBase):
    """Базовый класс для всех таблиц SQLAlchemy."""


def init_db() -> None:
    """Создаёт таблицы, которых ещё нет.

    Миграций в MVP нет. Чтобы демо не падало на старой базе после изменения таблиц, для SQLite
    храним отпечаток схемы: если он изменился, база пересоздаётся (демо-данные при этом теряются).
    """
    import app.models.orm  # noqa: F401  — регистрирует таблицы в Base.metadata

    with engine.begin() as conn:
        if IS_SQLITE:
            fingerprint = _fingerprint()
            conn.execute(text(f"CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} (value TEXT)"))
            stored = conn.execute(text(f"SELECT value FROM {SCHEMA_TABLE}")).scalar()
            if stored != fingerprint:
                existing = set(inspect(conn).get_table_names()) & set(Base.metadata.tables)
                if existing:
                    log.warning("Схема таблиц изменилась — пересоздаю базу %s", settings.database_url)
                    Base.metadata.drop_all(conn)
                conn.execute(text(f"DELETE FROM {SCHEMA_TABLE}"))
                conn.execute(text(f"INSERT INTO {SCHEMA_TABLE} (value) VALUES (:v)"), {"v": fingerprint})
        Base.metadata.create_all(conn)


def _fingerprint() -> str:
    """Отпечаток схемы: таблицы, колонки, типы и допустимые значения enum'ов."""
    parts = []
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            enums = ",".join(getattr(col.type, "enums", None) or [])
            parts.append(f"{table.name}.{col.name}:{type(col.type).__name__}:{col.nullable}:{enums}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def get_db() -> Iterator[Session]:
    """Зависимость FastAPI: открывает сессию на время запроса и закрывает после."""
    with SessionLocal() as session:
        yield session
