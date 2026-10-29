"""Демо для защиты: заполнить очередь сотрудника готовыми кейсами. В рабочей версии этой ручки нет."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import demo

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/seed")
def seed(db: Session = Depends(get_db)):
    """Создаёт три демо-кейса (честный, дроппер, скрытый доход) в статусе «отправлен»."""
    return {"case_ids": demo.seed(db)}
