"""Рабочее место сотрудника: очередь кейсов, готовая сверка, структурированный запрос и решение."""

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_case
from app.db import repository
from app.db.session import get_db
from app.models.enums import CaseStatus
from app.models.orm import Case, utcnow
from app.models.schemas import CaseOut, DecisionIn, DocsRequest, MessageIn, MessageOut
from app.services import conversation
from app.services.bank.transactions import pull_transactions
from app.services.review.report import build_report, suggest_request

router = APIRouter(prefix="/api/staff", tags=["staff"])

REVIEWABLE = (CaseStatus.submitted, CaseStatus.in_review)


@router.get("/cases", response_model=list[CaseOut])
def queue(status: CaseStatus | None = Query(None), db: Session = Depends(get_db)):
    """Очередь кейсов: сначала отправленные, по времени отправки. Можно отфильтровать по статусу."""
    return repository.list_cases(db, status)


@router.get("/cases/{case_id}/report")
def report(case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Полная сверка по кейсу: индикаторы, вердикт и черновик запроса.

    Первое открытие переводит кейс из «отправлен» в «на проверке».
    """
    if case.status is CaseStatus.submitted:
        case.status = CaseStatus.in_review
        repository.log_event(db, case, "staff", "review_started")
        db.commit()
    started = time.perf_counter()
    data = build_report(case)
    data["request_draft"] = suggest_request(case, data)
    data["build_ms"] = round((time.perf_counter() - started) * 1000)  # сколько заняла автоматическая сверка
    data["documents"] = [{"id": d.id, "doc_type": d.doc_type.value, "filename": d.filename, "uploaded_at": d.uploaded_at}
                         for d in sorted(case.documents, key=lambda d: d.uploaded_at)]
    return data


@router.get("/cases/{case_id}/documents/{doc_id}")
def download_document(doc_id: int, case: Case = Depends(get_case)):
    """Файл, который загрузил клиент: справка, договор, пояснение или выгрузка чеков."""
    doc = next((d for d in case.documents if d.id == doc_id), None)
    if not doc or not Path(doc.path).exists():
        raise HTTPException(404, "Документ не найден")
    return FileResponse(doc.path, filename=doc.filename)


@router.get("/cases/{case_id}/messages", response_model=list[MessageOut])
def list_messages(case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Переписка с клиентом. Сотрудник видит и свои обоснования решений."""
    return conversation.feed(repository.list_events(db, case.id), for_staff=True)


@router.post("/cases/{case_id}/messages", response_model=list[MessageOut], status_code=201)
def send_message(body: MessageIn, case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Сотрудник пишет клиенту. Возвращает обновлённую ленту."""
    repository.log_event(db, case, "staff", "message", {"text": body.text.strip()})
    db.commit()
    return conversation.feed(repository.list_events(db, case.id), for_staff=True)


@router.post("/cases/{case_id}/transactions/sync")
def sync_transactions(case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Заново подтягивает операции по карте из банка. Пояснения клиента к операциям сохраняются."""
    loaded = pull_transactions(case)
    repository.log_event(db, case, "staff", "transactions_synced", {"count": loaded})
    db.commit()
    return {"transactions": loaded}


@router.post("/cases/{case_id}/request-docs", response_model=CaseOut)
def request_docs(body: DocsRequest, case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Отправляет клиенту точечный запрос: свой список или черновик системы.

    Кейс возвращается клиенту, счётчик кругов запросов растёт.
    """
    if case.status not in REVIEWABLE:
        raise HTTPException(409, f"Нельзя запросить документы в статусе {case.status.value}")
    items = body.items or suggest_request(case, build_report(case))
    if not items:
        raise HTTPException(422, "Нечего запрашивать: пакет полный, примите решение")
    case.status = CaseStatus.docs_requested
    case.request_rounds += 1
    repository.log_event(db, case, "staff", "docs_requested", {"items": items, "comment": body.comment, "round": case.request_rounds})
    db.commit()
    db.refresh(case)
    return case


@router.post("/cases/{case_id}/decision", response_model=CaseOut)
def decide(body: DecisionIn, case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Решение по кейсу: разблокировать или отказать.

    В журнал сохраняются вердикт и индикаторы, на которых принято решение.
    """
    if case.status not in REVIEWABLE:
        raise HTTPException(409, f"Нельзя принять решение в статусе {case.status.value}")
    data = build_report(case)
    case.status = CaseStatus.unblocked if body.decision == "unblock" else CaseStatus.rejected
    case.decided_at = utcnow()
    # сохраняем, на каких данных принято решение: пригодится и для аудита, и для калибровки порогов
    repository.log_event(db, case, "staff", "decided", {
        "decision": body.decision,
        "comment": body.comment,
        "verdict": data["verdict"],
        "indicators": {i["code"]: i["level"] for i in data["indicators"]},
    })
    db.commit()
    db.refresh(case)
    return case
