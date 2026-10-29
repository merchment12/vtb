"""Общие зависимости роутов."""

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import repository
from app.db.session import get_db
from app.models.orm import Case


def get_case(case_id: int, db: Session = Depends(get_db)) -> Case:
    """Достаёт кейс по id из пути вместе с документами, операциями и чеками. Нет такого — 404."""
    case = repository.get_case(db, case_id)
    if not case:
        raise HTTPException(404, "Кейс не найден")
    return case
