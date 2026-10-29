"""Сценарий клиента: собрать полный пакет, увидеть, чего не хватает, и отправить один раз."""

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_case
from app.db import repository
from app.db.session import get_db
from app.models.enums import CaseStatus, DocType, TxCategory
from app.models.orm import Case, utcnow
from app.models.schemas import CaseCreate, CaseOut, EventOut, MessageIn, MessageOut, TxOut, TxUpdate, UploadResult
from app.services import conversation, npd
from app.services.bank.transactions import pull_transactions
from app.services.documents.parsers import ParseError
from app.services.documents.upload import attach_document
from app.services.review.report import build_report

router = APIRouter(prefix="/api/cases", tags=["client"])

EDITABLE = (CaseStatus.draft, CaseStatus.docs_requested)
MAX_UPLOAD = 10 * 1024 * 1024


def _ensure_editable(case: Case) -> None:
    """409, если кейс уже отправлен или закрыт и менять пакет нельзя."""
    if case.status not in EDITABLE:
        raise HTTPException(409, f"Кейс в статусе {case.status.value}, менять пакет сейчас нельзя")


@router.post("", response_model=CaseOut, status_code=201)
def create_case(body: CaseCreate, db: Session = Depends(get_db)):
    """Создаёт кейс: проверяет ИНН, запрашивает статус НПД в ФНС (если включено), подтягивает операции по карте из банка."""
    case = Case(**body.model_dump(), npd_status=npd.check_status(body.inn))
    db.add(case)
    db.flush()
    repository.log_event(db, case, "client", "created", {"block_reason": case.block_reason.value})
    loaded = pull_transactions(case)
    repository.log_event(db, case, "system", "transactions_loaded", {"count": loaded})
    db.commit()
    db.refresh(case)
    return case


@router.get("/{case_id}", response_model=CaseOut)
def read_case(case: Case = Depends(get_case)):
    """Кейс со статусом и списком загруженных документов."""
    return case


@router.post("/{case_id}/documents", response_model=UploadResult)
async def upload_document(
    doc_type: DocType = Query(..., description="receipts разбираются автоматически, остальное сохраняется как файл"),
    file: UploadFile = File(...),
    case: Case = Depends(get_case),
    db: Session = Depends(get_db),
):
    """Загружает документ от клиента: чеки «Мой налог», справки, договоры, пояснения.

    Чеки разбираются сразу и заменяют прошлую выгрузку, остальное просто сохраняется. Выписку клиент
    не загружает: операции по карте банк берёт из своей системы. В ответе — сколько чеков разобрано
    и можно ли уже отправлять пакет.
    """
    _ensure_editable(case)
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "Файл больше 10 МБ")
    filename = Path(file.filename or "file").name

    try:
        doc, parsed = attach_document(case, doc_type, filename, content)
    except ParseError as exc:
        raise HTTPException(422, str(exc)) from exc

    repository.log_event(db, case, "client", "uploaded", {"doc_type": doc_type.value, "parsed": parsed})
    db.commit()
    db.refresh(case)
    return UploadResult(document=doc, parsed=parsed, ready_to_submit=build_report(case)["ready_to_submit"])


@router.get("/{case_id}/transactions", response_model=list[TxOut])
def list_transactions(
    only_unmatched: bool = Query(False, description="Только поступления без чека"),
    case: Case = Depends(get_case),
):
    """Операции по карте из банка, по времени. Клиент видит их, но не меняет — только поясняет.

    С only_unmatched — только то, что клиенту нужно пояснить: поступления без чека и личные переводы.
    """
    if not only_unmatched:
        return sorted(case.transactions, key=lambda t: t.operated_at)
    unmatched = {x["id"] for x in build_report(case)["matching"]["unmatched_income"]}
    personal = {t.id for t in case.transactions if t.category is TxCategory.personal}
    return sorted((t for t in case.transactions if t.id in unmatched | personal), key=lambda t: t.operated_at)


@router.patch("/{case_id}/transactions/{tx_id}", response_model=TxOut)
def explain_transaction(tx_id: int, body: TxUpdate, case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Клиент поясняет операцию: например, «перевод от мамы» вместо дохода от деятельности."""
    _ensure_editable(case)
    tx = next((t for t in case.transactions if t.id == tx_id), None)
    if not tx:
        raise HTTPException(404, "Операция не найдена")
    if body.category:
        tx.category = TxCategory(body.category)
    if body.client_comment is not None:
        tx.client_comment = body.client_comment.strip() or None
    repository.log_event(db, case, "client", "explained", {"tx_id": tx.id, "category": tx.category.value})
    db.commit()
    return tx


@router.get("/{case_id}/checklist")
def get_checklist(case: Case = Depends(get_case)):
    """Что ещё нужно до отправки. Это то, что клиент видит в приложении."""
    report = build_report(case)
    return {
        "ready_to_submit": report["ready_to_submit"],
        "checklist": report["checklist"],
        "unmatched_income": report["matching"]["unmatched_income"],
        # клиенту показываем только, сколько осталось пояснить, а не значения индикаторов риска (115-ФЗ)
        "left_to_explain": sum(1 for x in report["matching"]["unmatched_income"] if not x["client_comment"]),
    }


@router.post("/{case_id}/submit", response_model=CaseOut)
def submit(case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Отправляет пакет сотруднику. Если обязательные пункты чек-листа не закрыты — 409 со списком."""
    _ensure_editable(case)
    report = build_report(case)
    if not report["ready_to_submit"]:
        missing = [i["title"] for i in report["checklist"] if i["required"] and not i["done"]]
        raise HTTPException(409, {"message": "Пакет неполный", "missing": missing})
    case.status = CaseStatus.submitted
    case.submitted_at = case.submitted_at or utcnow()
    repository.log_event(db, case, "client", "submitted", {"round": case.request_rounds})
    db.commit()
    db.refresh(case)
    return case


@router.get("/{case_id}/events", response_model=list[EventOut])
def list_events(case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Журнал кейса: кто и что делал, по порядку."""
    return repository.list_events(db, case.id)


@router.get("/{case_id}/messages", response_model=list[MessageOut])
def list_messages(case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Переписка с банком: сообщения, запросы документов и итог проверки. Индикаторов риска здесь нет."""
    return conversation.feed(repository.list_events(db, case.id), for_staff=False)


@router.post("/{case_id}/messages", response_model=list[MessageOut], status_code=201)
def send_message(body: MessageIn, case: Case = Depends(get_case), db: Session = Depends(get_db)):
    """Клиент пишет сотруднику. Возвращает обновлённую ленту."""
    repository.log_event(db, case, "client", "message", {"text": body.text.strip()})
    db.commit()
    return conversation.feed(repository.list_events(db, case.id), for_staff=False)
