"""Эмуляция учётной системы банка (АБС): операции по картам клиента.

В рабочей версии здесь запрос к АБС ВТБ по ИНН клиента. В MVP операции лежат файлами
bank_core_dir/<ИНН>.csv или .xlsx в формате выписки. Клиент к этим данным доступа не имеет.
"""

from datetime import date, timedelta
from pathlib import Path

from app.core.config import settings
from app.services.documents.parsers import ParsedTx, parse_statement


def store_statement(inn: str, filename: str, content: bytes) -> int:
    """Кладёт операции клиента в эмулятор. Сначала разбирает файл, чтобы не сохранить битый. Возвращает число операций."""
    rows = parse_statement(content, filename)
    folder = Path(settings.bank_core_dir)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{inn}.*"):
        old.unlink()
    (folder / f"{inn}{Path(filename).suffix.lower()}").write_bytes(content)
    return len(rows)


def fetch_transactions(inn: str, until: date) -> list[ParsedTx]:
    """Операции клиента за bank_history_days дней до даты until. Данных по клиенту нет — пустой список."""
    files = sorted(Path(settings.bank_core_dir).glob(f"{inn}.*"))
    if not files:
        return []
    rows = parse_statement(files[0].read_bytes(), files[0].name)
    since = until - timedelta(days=settings.bank_history_days)
    return [r for r in rows if since <= r.operated_at.date() <= until]
