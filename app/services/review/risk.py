"""Индикаторы риска по операциям самозанятого.

Это не автоматическое решение, а набор фактов с порогами, на которые сотрудник опирается
при разборе. Признаки взяты из типичных оснований для запросов по 115-ФЗ и рекомендаций ЦБ
о сомнительных операциях: транзит, обналичивание, доход без чеков, превышение лимита НПД.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from app.core.config import settings
from app.models.enums import CancelReason, IncomeType, TxCategory, TxDirection
from app.models.orm import Case, Receipt, Transaction
from app.services.review.matching import MatchResult

# ставки НПД по incomeType чека; вычет 10 000 ₽ снижает их на 1–2 п. п., на это запас в пороге 0,5
NPD_RATES = {IncomeType.individual: 0.04, IncomeType.legal_entity: 0.06, IncomeType.foreign_agency: 0.06}


@dataclass
class Indicator:
    """Индикатор риска: значение, порог, уровень (ok / info / warn / high), объяснение и операции-основания."""

    code: str
    title: str
    level: str  # ok / info / warn / high
    value: float | str | None
    threshold: float | str | None
    explanation: str
    tx_ids: list[int] = field(default_factory=list)


def _rub(value: float) -> str:
    """Сумма в рублях для текста: «12 500 ₽»."""
    return f"{value:,.0f} ₽".replace(",", " ")


MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование с числом: 1 операция, 3 операции, 5 операций."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _ops(n: int) -> str:
    """«3 операции», «1 операция»."""
    return f"{n} {_plural(n, 'операция', 'операции', 'операций')}"


def _level(value: float, warn: float, high: float | None = None, higher_is_worse: bool = True) -> str:
    """Уровень по порогам warn/high. higher_is_worse=False — для метрик, где плохо, когда мало."""
    if higher_is_worse:
        if high is not None and value >= high:
            return "high"
        return "warn" if value >= warn else "ok"
    if high is not None and value <= high:
        return "high"
    return "warn" if value <= warn else "ok"


def evaluate(case: Case, txs: list[Transaction], receipts: list[Receipt], m: MatchResult) -> list[Indicator]:
    """Считает все индикаторы риска по операциям и чекам кейса."""
    active_receipts = [r for r in receipts if not r.cancelled]
    income = [t for t in txs if t.direction is TxDirection.credit and t.category is TxCategory.business_income]
    income_sum = sum(t.amount for t in income)
    matched_tx = {tid for mt in m.matches for tid in mt.tx_ids}
    # поступления, которые клиент сам перевёл из «дохода от деятельности» в личные: они выпадают из покрытия чеками,
    # поэтому без отдельного индикатора так можно спрятать доход без чеков
    personal = [t for t in txs if t.direction is TxDirection.credit and t.category is TxCategory.personal]
    relabeled = [t for t in personal if t.auto_category is TxCategory.business_income]
    out: list[Indicator] = []

    # 1. покрытие дохода чеками
    if income_sum:
        covered = sum(t.amount for t in income if t.id in matched_tx) / income_sum
        uncovered = [t.id for t in income if t.id not in matched_tx]
        out.append(Indicator(
            "receipt_coverage", "Доход подтверждён чеками",
            _level(covered, settings.coverage_warn, settings.coverage_high, higher_is_worse=False),
            round(covered, 3), settings.coverage_warn,
            f"{covered:.0%} суммы поступлений от деятельности сопоставлено с чеками. "
            f"Без чека: {_ops(len(uncovered))}."
            + (f" Ещё {len(relabeled)} {_plural(len(relabeled), 'поступление', 'поступления', 'поступлений')} "
               f"клиент отнёс к личным — в покрытие {'оно не входит' if len(relabeled) == 1 else 'они не входят'}, "
               "см. индикатор «Поступления, отнесённые клиентом к личным»." if relabeled else ""),
            uncovered,
        ))

    # 1b. сколько поступлений клиент сам отнёс к личным
    incoming = [t for t in txs if t.direction is TxDirection.credit
                and t.category not in (TxCategory.own_transfer, TxCategory.refund)]
    incoming_sum = sum(t.amount for t in incoming)
    if personal and incoming_sum:
        share = sum(t.amount for t in personal) / incoming_sum
        out.append(Indicator(
            "personal_relabel", "Поступления, отнесённые клиентом к личным",
            _level(share, settings.personal_share_warn, settings.personal_share_high),
            round(share, 3), settings.personal_share_warn,
            f"{share:.0%} суммы входящих переводов клиент назвал личными: {_ops(len(personal))} на "
            f"{_rub(sum(t.amount for t in personal))}. Из них система изначально определила как доход от "
            f"деятельности: {len(relabeled)}. Такие поступления не требуют чека — проверить комментарии и отправителей.",
            [t.id for t in personal],
        ))

    # 2. лимит НПД 2,4 млн в календарный год
    by_year: dict[int, float] = defaultdict(float)
    for r in active_receipts:
        by_year[r.operation_time.year] += r.amount
    income_by_year: dict[int, float] = defaultdict(float)
    for t in income:
        income_by_year[t.operated_at.year] += t.amount
    if by_year or income_by_year:
        peak = max(max(by_year.values(), default=0), max(income_by_year.values(), default=0))
        out.append(Indicator(
            "npd_limit", "Лимит дохода НПД (2,4 млн ₽ в год)",
            "high" if peak > settings.npd_annual_limit else ("warn" if peak > 0.9 * settings.npd_annual_limit else "ok"),
            round(peak, 2), settings.npd_annual_limit,
            "Максимум за календарный год по чекам или по поступлениям. "
            "При превышении человек теряет право на НПД.",
        ))

    # 3. транзит: какая доля поступлений ушла переводами/наличными в течение суток
    inflow = sorted((t for t in txs if t.direction is TxDirection.credit and t.category is not TxCategory.own_transfer),
                    key=lambda t: t.operated_at)
    outflow = sorted((t for t in txs if t.category in (TxCategory.transfer_out, TxCategory.cash)),
                     key=lambda t: t.operated_at)
    inflow_sum = sum(t.amount for t in inflow)
    if inflow_sum:
        remaining = {t.id: t.amount for t in inflow}
        moved = 0.0
        for d in outflow:
            left = d.amount
            for c in inflow:
                if c.operated_at > d.operated_at:
                    break
                if d.operated_at - c.operated_at > timedelta(hours=24) or remaining[c.id] <= 0:
                    continue
                take = min(left, remaining[c.id])
                remaining[c.id] -= take
                left -= take
                moved += take
                if left <= 0:
                    break
        ratio = moved / inflow_sum
        out.append(Indicator(
            "transit", "Транзит: выведено в течение 24 ч после поступления",
            _level(ratio, settings.transit_warn, settings.transit_high),
            round(ratio, 3), settings.transit_warn,
            f"{ratio:.0%} поступивших денег ушло переводами другим лицам или наличными в течение суток.",
        ))

    # 4. обналичивание
    debits = [t for t in txs if t.direction is TxDirection.debit]
    debit_sum = sum(t.amount for t in debits)
    if debit_sum:
        cash = [t for t in debits if t.category is TxCategory.cash]
        share = sum(t.amount for t in cash) / debit_sum
        out.append(Indicator(
            "cash_out", "Доля снятия наличных в расходах",
            _level(share, settings.cash_out_warn), round(share, 3), settings.cash_out_warn,
            f"{share:.0%} всех списаний — снятие наличных.",
            [t.id for t in cash],
        ))

    # 5. уплата налога
    if active_receipts and txs:
        start = min(t.operated_at for t in txs)
        end = max(t.operated_at for t in txs)
        expected = sum(r.amount * NPD_RATES.get(r.income_type, NPD_RATES[IncomeType.individual])
                       for r in active_receipts if r.operation_time <= end - timedelta(days=45))
        paid = sum(t.amount for t in txs if t.category is TxCategory.tax)
        if (end - start).days >= 45 and expected > 0:
            ratio = paid / expected
            out.append(Indicator(
                "tax_paid", "Уплата НПД с этого счёта",
                "warn" if ratio < 0.5 else "ok", f"{_rub(paid)} / {_rub(expected)}", "не меньше половины начисленного",
                f"Оплачено с этого счёта {_rub(paid)}, начислено {_rub(expected)} — 4% с физлиц и 6% с юрлиц "
                "по чекам, срок уплаты которых уже наступил. "
                + ("Оплачено больше начисленного: налог мог быть уплачен заранее (за последние недели) или за периоды до начала выписки — это не нарушение."
                   if ratio > 1 else
                   "Налог мог платиться с другой карты — уточнить у клиента." if ratio < 0.5 else
                   "Налог платится с этого счёта."),
            ))

    # 6. много разных плательщиков
    payers: dict[str, set[str]] = defaultdict(set)
    for t in [*income, *personal]:  # личные тоже считаем: иначе плательщиков можно спрятать перекатегоризацией
        if t.counterparty:
            payers[t.operated_at.strftime("%Y-%m")].add(t.counterparty.strip().lower())
    if payers:
        peak_month, peak_set = max(payers.items(), key=lambda kv: len(kv[1]))
        out.append(Indicator(
            "payers_per_month", "Уникальных отправителей в месяц (доход и личные переводы, максимум)",
            "warn" if len(peak_set) >= settings.payers_per_month_warn else "ok",
            len(peak_set), settings.payers_per_month_warn,
            f"Пик — {MONTHS[int(peak_month[5:]) - 1]} {peak_month[:4]}. Для репетиторов и мастеров много клиентов нормально, "
            "смотреть вместе с покрытием чеками и транзитом.",
        ))

    # 7. ночные операции (только если в выписке есть время)
    with_time = [t for t in txs if t.operated_at.time().hour or t.operated_at.time().minute]
    if len(with_time) >= len(txs) * 0.5 and txs:
        night = [t for t in txs if t.operated_at.hour < 6]
        share = len(night) / len(txs)
        out.append(Indicator(
            "night_share", "Операции с 00:00 до 06:00",
            _level(share, settings.night_share_warn), round(share, 3), settings.night_share_warn,
            f"{share:.0%} операций совершено ночью.",
            [t.id for t in night],
        ))

    # 8. аннулированные чеки
    if receipts:
        cancelled = [r for r in receipts if r.cancelled]
        share = len(cancelled) / len(receipts)
        mistakes = sum(r.cancel_reason == CancelReason.mistake for r in cancelled)
        refunds = sum(r.cancel_reason == CancelReason.refund for r in cancelled)
        out.append(Indicator(
            "cancelled_receipts", "Аннулированные чеки",
            _level(share, settings.cancelled_share_warn), round(share, 3), settings.cancelled_share_warn,
            f"Аннулировано {len(cancelled)} из {len(receipts)} чеков: «ошибочно» — {mistakes}, "
            f"возврат средств — {refunds}. Массовое аннулирование «ошибочно» — способ занизить доход.",
        ))

    # 9. чеки выбиты не этим ИНН
    foreign = [r for r in receipts if r.seller_inn and r.seller_inn != case.inn]
    if foreign:
        out.append(Indicator(
            "seller_inn_mismatch", "Чеки сформированы другим ИНН", "high", len(foreign), 0,
            "В пакете есть чеки, где продавец не совпадает с клиентом.",
        ))

    # 10. чеки позже срока (информативно)
    if m.late_receipt_ids:
        out.append(Indicator(
            "late_receipts", "Чеки сформированы позже дня оплаты", "info", len(m.late_receipt_ids), None,
            "При безналичной оплате чек нужен в момент расчёта. Это нарушение 422-ФЗ, "
            "но само по себе не признак сомнительной операции.",
        ))

    # 11. статус НПД в ФНС
    if case.npd_status == "inactive":
        out.append(Indicator(
            "npd_status", "Статус НПД в ФНС", "high", case.npd_status, "active",
            "По данным ФНС человек не является плательщиком НПД.",
        ))
    elif case.npd_status in (None, "unknown"):
        out.append(Indicator(
            "npd_status", "Статус НПД в ФНС", "info", case.npd_status or "unknown", "active",
            "Статус не проверен автоматически (в демо запрос к ФНС выключен) — сверить со справкой КНД 1122035.",
        ))
    return out


def overall(indicators: list[Indicator]) -> tuple[str, str]:
    """Итоговый вердикт по индикаторам (high_risk / needs_attention / low_risk) и его текст."""
    highs = [i for i in indicators if i.level == "high"]
    warns = [i for i in indicators if i.level == "warn"]
    if highs:
        return "high_risk", "Есть серьёзные признаки: " + "; ".join(i.title for i in highs) + ". Нужен углублённый разбор."
    if len(warns) >= 2:
        return "needs_attention", "Несколько отклонений: " + "; ".join(i.title for i in warns) + ". Уточнить у клиента."
    if warns:
        return "low_risk", f"Одно отклонение ({warns[0].title}), в остальном профиль обычного самозанятого."
    return "low_risk", "Доход подтверждён чеками, признаков транзита и обналичивания нет."
