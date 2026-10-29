"""Подключение к БД: engine, фабрика сессий и базовый класс таблиц."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Базовый класс для всех таблиц SQLAlchemy."""


def init_db() -> None:
    """Создаёт таблицы, которых ещё нет. Миграций нет: после изменения таблиц удалите unblock.db."""
    import app.models.orm  # noqa: F401  — регистрирует таблицы в Base.metadata

    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    """Зависимость FastAPI: открывает сессию на время запроса и закрывает после."""
    with SessionLocal() as session:
        yield session
