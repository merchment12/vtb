"""Собирает все роутеры в один api_router, который подключается в main.py."""

from fastapi import APIRouter

from app.api.routes import analytics, bank_core, client, demo, staff

api_router = APIRouter()
api_router.include_router(client.router)
api_router.include_router(staff.router)
api_router.include_router(analytics.router)
api_router.include_router(bank_core.router)
api_router.include_router(demo.router)
