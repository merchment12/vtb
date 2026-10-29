"""Сводка по кейсу: то, что сейчас сотрудник собирает руками, листая выписку и чеки."""

from dataclasses import asdict

from app.models.enums import TxCategory, TxDirection
from app.models.orm import Case
from app.services.review import checklist, risk
from app.services.review.matching import MatchResult, match

MAX_TX_LINES = 15


def run_matching(case: Case) -> MatchResult:
    """Сверка по кейсу: только поступления от деятельности и только действующие чеки."""
    credits = [t for t in case.transactions
               if t.direction is TxDirection.credit and t.category is TxCategory.business_income]
    receipts = [r for r in case.receipts if not r.cancelled]
    return match(credits, receipts)


def build_report(case: Case) -> dict:
    """Полная сводка по кейсу: суммы, сверка, индикаторы, вердикт и чек-лист."""
    m = run_matching(case)
    indicators = risk.evaluate(case, case.transactions, case.receipts, m)
    verdict, verdict_text = risk.overall(indicators)
    items = checklist.build(case, m)

    txs = case.transactions
    by_cat: dict[str, float] = {}
    for t in txs:
        by_cat[t.category.value] = round(by_cat.get(t.category.value, 0) + t.amount, 2)
    tx_by_id = {t.id: t for t in txs}
    rc_by_id = {r.id: r for r in case.receipts}

    return {
        "case_id": case.id,
        "full_name": case.full_name,
        "inn": case.inn,
        "block_reason": case.block_reason.value,
        "blocked_at": case.blocked_at,
        "request_rounds": case.request_rounds,
        "status": case.status.value,
        "period": {
            "start": min(t.operated_at for t in txs).date() if txs else None,
            "end": max(t.operated_at for t in txs).date() if txs else None,
        },
        "totals": {
            "credits": round(sum(t.amount for t in txs if t.direction is TxDirection.credit), 2),
            "debits": round(sum(t.amount for t in txs if t.direction is TxDirection.debit), 2),
            "receipts": round(sum(r.amount for r in case.receipts if not r.cancelled), 2),
            "by_category": by_cat,
        },
        "matching": {
            "matched": len(m.matches),
            "matches": [asdict(x) for x in m.matches],
            # те же пары, но с данными операций и чеков — для экрана сотрудника
            "pairs": [
                {
                    "kind": x.kind,
                    "days_gap": x.days_gap,
                    "payments": [{"id": t.id, "operated_at": t.operated_at, "amount": t.amount,
                                  "counterparty": t.counterparty} for t in (tx_by_id[i] for i in x.tx_ids)],
                    "receipts": [{"id": r.id, "operation_time": r.operation_time, "amount": r.amount,
                                  "service": r.service} for r in (rc_by_id[i] for i in x.receipt_ids)],
                    "late": any(i in m.late_receipt_ids for i in x.receipt_ids),
                }
                for x in sorted(m.matches, key=lambda x: min(tx_by_id[i].operated_at for i in x.tx_ids))
            ],
            "unmatched_income": [
                {"id": t.id, "operated_at": t.operated_at, "amount": t.amount,
                 "counterparty": t.counterparty, "purpose": t.purpose, "client_comment": t.client_comment}
                for t in (tx_by_id[i] for i in m.unmatched_tx_ids)
            ],
            "personal_income": [
                {"id": t.id, "operated_at": t.operated_at, "amount": t.amount, "counterparty": t.counterparty,
                 "client_comment": t.client_comment,
                 "relabeled": t.auto_category is TxCategory.business_income}
                for t in sorted(txs, key=lambda t: t.operated_at)
                if t.direction is TxDirection.credit and t.category is TxCategory.personal
            ],
            "receipts_without_payment": [
                {"id": r.id, "operation_time": r.operation_time, "amount": r.amount, "service": r.service,
                 "note": "Оплата могла прийти наличными или в другой банк"}
                for r in (rc_by_id[i] for i in m.unmatched_receipt_ids)
            ],
        },
        "indicators": [asdict(i) for i in indicators],
        "verdict": verdict,
        "verdict_text": verdict_text,
        "checklist": [asdict(i) for i in items],
        "ready_to_submit": checklist.is_ready(items),
    }


def suggest_request(case: Case, report: dict) -> list[str]:
    """Черновик запроса клиенту: конкретные операции и документы вместо общей формулировки."""
    lines = []
    for item in report["checklist"]:
        if item["code"] == "bank_transactions":
            continue  # операции подтягивает банк, у клиента их не запрашиваем
        if not item["done"] and (item["required"] or item["tx_ids"]):
            lines.append(f"{item['title']}. {item['hint'] or ''}".strip())
    unexplained = [tx for tx in report["matching"]["unmatched_income"] if not tx["client_comment"]]
    for tx in unexplained[:MAX_TX_LINES]:
        lines.append(
            f"Поступление {tx['amount']:.2f} ₽ от {tx['operated_at']:%d.%m.%Y}"
            f"{' (' + tx['counterparty'] + ')' if tx['counterparty'] else ''}: приложите чек или поясните назначение."
        )
    if len(unexplained) > MAX_TX_LINES:
        lines.append(f"И ещё {len(unexplained) - MAX_TX_LINES} поступлений без чека — полный список в приложении.")
    for ind in report["indicators"]:
        if ind["level"] in ("warn", "high") and ind["code"] in ("transit", "cash_out"):
            lines.append(f"{ind['title']}: {ind['explanation']} Поясните, на какие цели расходовались средства.")
        if ind["level"] == "warn" and ind["code"] == "tax_paid":
            lines.append("Приложите подтверждение уплаты НПД, если налог платился с другого счёта.")
    return lines
