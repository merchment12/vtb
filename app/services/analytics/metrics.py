"""Метрики пилота: из них берутся реальные цифры для финмодели вместо допущений."""

from collections import Counter

from app.models.enums import CaseStatus
from app.models.orm import Case


def summary(cases: list[Case]) -> dict:
    """Сводные метрики по списку кейсов. Время до решения считается от отправки пакета."""
    decided = [c for c in cases if c.decided_at and c.submitted_at]
    hours = [(c.decided_at - c.submitted_at).total_seconds() / 3600 for c in decided]
    return {
        "cases_total": len(cases),
        "by_status": dict(Counter(c.status.value for c in cases)),
        "decided": len(decided),
        "avg_hours_to_decision": round(sum(hours) / len(hours), 2) if hours else None,
        "avg_request_rounds": round(sum(c.request_rounds for c in decided) / len(decided), 2) if decided else None,
        "share_without_repeat_request": (
            round(sum(c.request_rounds == 0 for c in decided) / len(decided), 3) if decided else None
        ),
        "unblock_rate": (
            round(sum(c.status is CaseStatus.unblocked for c in decided) / len(decided), 3) if decided else None
        ),
    }
