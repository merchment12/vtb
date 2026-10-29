"""Метрики пилота и финмодель."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import repository
from app.db.session import get_db
from app.services.analytics import finmodel, metrics
from app.services.analytics.finmodel import FinInput

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/analytics/summary")
def summary(db: Session = Depends(get_db)):
    """Метрики пилота по всем кейсам: время до решения, круги запросов, доля разблокировок."""
    return metrics.summary(repository.list_cases(db))


@router.post("/finmodel")
def calculate_finmodel(body: FinInput):
    """Считает финмодель по переданным допущениям. Пустое тело — значения по умолчанию."""
    return {"input": body, "result": finmodel.calculate(body)}


@router.get("/finmodel/scenarios")
def scenarios():
    """Пессимистичный / базовый / оптимистичный по самым спорным допущениям."""
    return finmodel.scenarios()
