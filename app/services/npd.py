"""Проверка статуса самозанятого через открытый сервис ФНС statusnpd.nalog.ru."""

import logging
from datetime import date

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)


def check_status(inn: str, on: date | None = None) -> str:
    """active / inactive / unknown. Ошибка сети не должна ломать сценарий клиента."""
    if not settings.npd_check_enabled:
        return "unknown"
    try:
        resp = httpx.post(
            settings.npd_check_url,
            json={"inn": inn, "requestDate": (on or date.today()).isoformat()},
            timeout=10,
        )
        resp.raise_for_status()
        return "active" if resp.json().get("status") else "inactive"
    except httpx.HTTPError as exc:
        log.warning("NPD status check failed for %s: %s", inn, exc)
        return "unknown"
