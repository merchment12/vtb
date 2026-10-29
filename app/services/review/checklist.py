"""Проверка полноты пакета до отправки.

Главная причина долгой разблокировки, по словам самого ВТБ (ТАСС, июль 2025), — неполные
пояснения и документы, из-за которых банк отправляет повторный запрос. Поэтому кейс нельзя
отправить, пока не закрыты все обязательные пункты.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from app.models.enums import BlockReason, DocType, TxCategory, TxDirection
from app.models.orm import Case
from app.services.review.matching import MatchResult

STATEMENT_MIN_DAYS = 90
LARGE_PAYMENT = 100_000

DOC_TITLES = {
    DocType.receipts: "Чеки из «Мой налог»",
    DocType.npd_certificate: "Справка о постановке на учёт НПД (КНД 1122035)",
    DocType.income_certificate: "Справка о доходах НПД (КНД 1122036)",
    DocType.contract: "Договоры с крупными заказчиками",
    DocType.act: "Акты выполненных работ",
    DocType.explanation: "Письменное пояснение",
}

# что обязательно для каждого основания запроса; остальное рекомендовано
REQUIRED_DOCS: dict[BlockReason, set[DocType]] = {
    BlockReason.income_source: {DocType.receipts, DocType.npd_certificate, DocType.income_certificate},
    BlockReason.p2p_volume: {DocType.receipts, DocType.npd_certificate, DocType.income_certificate},
    BlockReason.transit: {DocType.receipts, DocType.npd_certificate, DocType.explanation},
    BlockReason.cash_out: {DocType.receipts, DocType.npd_certificate, DocType.explanation},
}
RECOMMENDED_DOCS = [DocType.income_certificate, DocType.explanation, DocType.contract, DocType.act]

HINTS = {
    DocType.receipts: "«Мой налог» → Продажи → выгрузите чеки минимум за 3 месяца до блокировки.",
    DocType.npd_certificate: "«Мой налог» → Прочее → Справки → Справка о постановке на учёт.",
    DocType.income_certificate: "«Мой налог» → Прочее → Справки → Справка о доходах за нужный период.",
    DocType.explanation: "Опишите, на что тратились деньги: аренда, материалы, переводы родственникам и т. п.",
    DocType.contract: "Если были платежи от 100 000 ₽ — приложите договор с этим заказчиком.",
    DocType.act: "Акты по крупным заказам, если они оформлялись.",
}


@dataclass
class ChecklistItem:
    """Пункт чек-листа: обязательный ли, выполнен ли, подсказка и операции, к которым относится."""

    code: str
    title: str
    required: bool
    done: bool
    hint: str | None = None
    tx_ids: list[int] = field(default_factory=list)


def build(case: Case, m: MatchResult | None) -> list[ChecklistItem]:
    """Собирает чек-лист: документы, операции из банка, пояснения к поступлениям без чека и личным переводам."""
    uploaded = {d.doc_type for d in case.documents}
    required = REQUIRED_DOCS[case.block_reason]
    items: list[ChecklistItem] = []

    for doc in [DocType.receipts, DocType.npd_certificate, *RECOMMENDED_DOCS]:
        if any(i.code == f"doc:{doc.value}" for i in items):
            continue
        items.append(ChecklistItem(f"doc:{doc.value}", DOC_TITLES[doc], doc in required, doc in uploaded, HINTS.get(doc)))

    # операции по карте банк берёт сам; клиент их только видит и поясняет
    txs = case.transactions
    items.append(ChecklistItem(
        "bank_transactions", "Операции по карте получены из банка", True, bool(txs),
        None if txs else "Банк ещё не передал операции по карте. От вас ничего не нужно, сотрудник подтянет их сам.",
    ))
    if txs:
        start = min(t.operated_at for t in txs).date()
        anchor = case.blocked_at or max(t.operated_at for t in txs).date()
        need_from = anchor - timedelta(days=STATEMENT_MIN_DAYS)
        enough = start <= need_from
        # не обязательный: если счёт моложе 90 дней, клиент тут ничего не исправит
        items.append(ChecklistItem(
            "statement_period", f"Операции по карте за {STATEMENT_MIN_DAYS} дней до блокировки", False, enough,
            None if enough else f"Операции есть только с {start:%d.%m.%Y}, а нужны с {need_from:%d.%m.%Y}. "
                                "Сотрудник учтёт это при разборе.",
        ))

    if m is not None:
        by_id = {t.id: t for t in txs}
        # поступление без чека нужно либо подтвердить чеком, либо пояснить (перевод от родственника, возврат долга...)
        unexplained = [
            tid for tid in m.unmatched_tx_ids
            if not (by_id[tid].client_comment or "").strip()
        ]
        items.append(ChecklistItem(
            "unmatched_income", "Каждое поступление без чека пояснено", True, not unexplained,
            None if not unexplained else
            f"Поступлений без чека: {len(unexplained)}. Догрузите чеки или укажите, что это за деньги.",
            unexplained,
        ))

        large = [t for t in txs if t.direction is TxDirection.credit and t.category is TxCategory.business_income
                 and t.amount >= LARGE_PAYMENT]
        if large and DocType.contract not in uploaded:
            for item in items:
                if item.code == "doc:contract":
                    item.hint = f"Есть {len(large)} поступлений от {LARGE_PAYMENT:,} ₽ — договор сильно ускорит проверку.".replace(",", " ")
                    item.tx_ids = [t.id for t in large]

    personal_no_comment = [t.id for t in txs if t.category is TxCategory.personal and not (t.client_comment or "").strip()]
    if personal_no_comment:
        items.append(ChecklistItem(
            "personal_comment", "Личные поступления подписаны", True, False,
            "Для переводов, отмеченных как личные, нужен короткий комментарий: от кого и за что.",
            personal_no_comment,
        ))
    return items


def is_ready(items: list[ChecklistItem]) -> bool:
    """Пакет можно отправить, когда закрыты все обязательные пункты."""
    return all(i.done for i in items if i.required)
