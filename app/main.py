"""Точка входа: создаёт FastAPI-приложение и подключает роуты.

Запуск: uv run uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
        "Операции по карте банк берёт из своей системы, клиент приносит чеки «Мой налог» и пояснения. "
        "Система проверяет полноту пакета и сверяет поступления с чеками. Сотрудник получает готовую "
        "сверку с индикаторами риска.\n\n"
        "**Экраны: [клиент](/client) · [сотрудник](/staff) · [выбор роли](/)**"
    ),
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)


STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")  # общие стили и скрипты экранов


def _page(name: str) -> FileResponse:
    """HTML-страница из app/static."""
    return FileResponse(STATIC / name, media_type="text/html; charset=utf-8")


@app.get("/", tags=["экраны"], response_class=FileResponse)
def index():
    """Выбор роли для демо: клиент или сотрудник."""
    return _page("index.html")


@app.get("/client", tags=["экраны"], response_class=FileResponse)
def client_page():
    """Экран клиента: обращение, документы, пояснения к поступлениям и переписка с банком."""
    return _page("client.html")


@app.get("/staff", tags=["экраны"], response_class=FileResponse)
def staff_page():
    """Экран сотрудника: очередь кейсов, готовая сверка, переписка и решение."""
    return _page("staff.html")


@app.get("/health", tags=["service"])
def health():
    """Проверка, что сервис жив."""
    return {"status": "ok"}
