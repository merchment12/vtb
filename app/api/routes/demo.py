"""Демо для защиты: заполнить очередь сотрудника готовыми кейсами. В рабочей версии этой ручки нет."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_case
from app.db.session import get_db
from app.models.enums import CaseStatus
from app.models.orm import Case
from app.services import demo
from app.services.documents.parsers import ParseError

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/seed")
def seed(db: Session = Depends(get_db)):
    """Создаёт три демо-кейса (честный, дроппер, скрытый доход) в статусе «отправлен»."""
    return {"case_ids": demo.seed(db)}


@router.get("/clients")
def clients():
    """Демо-клиенты с операциями в эмуляторе банка — для формы нового обращения."""
    return demo.clients()


@router.post("/cases/{case_id}/fill-documents")
def fill_documents(case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Загружает за клиента обязательные документы синтетикой: чеки «Мой налог» и PDF-справки."""
    if case.status not in (CaseStatus.draft, CaseStatus.docs_requested):
        raise HTTPException(409, f"Кейс в статусе {case.status.value}, менять пакет сейчас нельзя")
    try:
        result = demo.fill_documents(db, case)
    except ParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return result
