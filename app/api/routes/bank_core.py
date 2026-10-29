"""Эмуляция учётной системы банка для демо. В рабочей версии этих ручек нет: операции уже лежат в АБС."""

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.validators import validate_inn
from app.services.bank import core
from app.services.documents.parsers import ParseError

router = APIRouter(prefix="/api/bank-core", tags=["bank-core (эмуляция)"])


@router.put("/{inn}/statement")
async def put_statement(inn: str, file: UploadFile = File(...)):
    """Кладёт операции по карте клиента в эмулятор банка (CSV или XLSX в формате выписки).

    Кейс, созданный после этого, подтянет их сам. Для уже созданного кейса — POST /api/staff/cases/{id}/transactions/sync.
    """
    if not validate_inn(inn):
        raise HTTPException(422, "Некорректный ИНН физлица")
    try:
        count = core.store_statement(inn, Path(file.filename or "statement.csv").name, await file.read())
    except ParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"inn": inn, "transactions": count}
