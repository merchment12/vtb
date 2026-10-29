"""Точка входа: создаёт FastAPI-приложение и подключает роуты.

Запуск: uv run uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

from app.api.routes import api_router
from app.db.session import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    """При старте приложения создаёт таблицы БД."""
    init_db()
    yield


app = FastAPI(
    title="Разблокировка самозанятых (115-ФЗ)",
    description=(
        "Клиент загружает выписку и чеки «Мой налог», система проверяет полноту пакета и сверяет "
        "поступления с чеками. Сотрудник получает готовую сверку с индикаторами риска.\n\n"
        "**Экран сотрудника: [/staff](/staff)** — нажмите «Загрузить демо-кейсы»."
    ),
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)


STAFF_PAGE = Path(__file__).resolve().parent / "static" / "staff.html"


@app.get("/", include_in_schema=False)
def index():
    """Корень ведёт на экран сотрудника."""
    return RedirectResponse("/staff")


@app.get("/staff", tags=["экран сотрудника"], response_class=FileResponse)
def staff_page():
    """Экран сотрудника: очередь кейсов и готовая сверка. Данные берёт из /api/staff."""
    return FileResponse(STAFF_PAGE, media_type="text/html; charset=utf-8")


@app.get("/health", tags=["service"])
def health():
    """Проверка, что сервис жив."""
    return {"status": "ok"}
