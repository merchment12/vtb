"""Переписка по кейсу: сообщения клиента и сотрудника, запросы документов и решения в одной ленте.

Отдельной таблицы нет: лента собирается из журнала кейса (case_events).
"""

from app.models.enums import DocType
from app.models.orm import CaseEvent
from app.services.review.checklist import DOC_TITLES

DOC_TYPE_VALUES = {d.value for d in DocType}
DECISION_TEXT = {"unblock": "Карта разблокирована. Спасибо, что быстро собрали документы.",
                 "reject": "По результатам проверки в разблокировке отказано."}


def feed(events: list[CaseEvent], for_staff: bool) -> list[dict]:
    """Лента сообщений по журналу кейса.

    Клиент видит переписку, запросы документов и итог. Внутренние комментарии сотрудника к решению
    и вердикт системы показываются только сотруднику: индикаторы риска клиенту не раскрываются.
    """
    out = []
    for e in events:
        p = e.payload or {}
        item = {"at": e.created_at, "from": e.actor, "text": None, "items": None}
        if e.kind == "message":
            item["text"] = p.get("text")
        elif e.kind in ("uploaded", "submitted", "review_started"):
            item["from"] = "system"  # служебные события — серой строкой, а не сообщением
            item["text"] = _status_text(e.kind, p)
        elif e.kind == "docs_requested":
            item["text"] = p.get("comment") or "Чтобы завершить проверку, дополните, пожалуйста, пакет:"
            item["items"] = p.get("items") or []
        elif e.kind == "decided":
            item["text"] = DECISION_TEXT.get(p.get("decision"), "Решение принято")
            if for_staff and p.get("comment"):
                item["text"] += f" Обоснование (видно только сотрудникам): {p['comment']}"
        else:
            continue
        out.append(item)
    return out


def _status_text(kind: str, p: dict) -> str:
    """Текст служебного события в ленте."""
    if kind == "uploaded":
        doc_type = p.get("doc_type")
        title = DOC_TITLES.get(DocType(doc_type)) if doc_type in DOC_TYPE_VALUES else None
        parsed = f", разобрано чеков: {p['parsed']}" if p.get("parsed") else ""
        return f"Клиент загрузил документ: {title or 'файл'}{parsed}"
    if kind == "submitted":
        return "Клиент отправил пакет на проверку"
    return "Сотрудник начал проверку"
