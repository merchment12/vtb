"""Все запросы к БД: роуты и сервисы не собирают select'ы сами."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import CaseStatus
from app.models.orm import Case, CaseEvent


def get_case(db: Session, case_id: int) -> Case | None:
    """Кейс по id с подгруженными документами, операциями и чеками. Нет такого — None."""
    return db.get(
        Case, case_id,
        options=[selectinload(Case.documents), selectinload(Case.transactions), selectinload(Case.receipts)],
    )


def list_cases(db: Session, status: CaseStatus | None = None) -> list[Case]:
    """Очередь сотрудника: сначала отправленные, по времени отправки."""
    stmt = select(Case).order_by(Case.submitted_at.is_(None), Case.submitted_at, Case.created_at)
    if status:
        stmt = stmt.where(Case.status == status)
    return list(db.scalars(stmt).all())


def list_events(db: Session, case_id: int) -> list[CaseEvent]:
    """События кейса в хронологическом порядке."""
    stmt = select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at)
    return list(db.scalars(stmt).all())


def log_event(db: Session, case: Case, actor: str, kind: str, payload: dict | None = None) -> None:
    """Добавляет событие в журнал кейса. Коммит делает вызывающий код."""
    db.add(CaseEvent(case_id=case.id, actor=actor, kind=kind, payload=payload))
