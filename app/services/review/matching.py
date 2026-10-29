"""Сопоставление поступлений из выписки с чеками «Мой налог».

1. Один к одному: совпадает сумма (с допуском), чек попадает в допустимое окно по дате.
   Пары с наименьшим расхождением по времени выбираются первыми.
2. Группы: несколько поступлений за день под один общий чек и наоборот,
   один платёж, по которому выбито несколько чеков.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations
from typing import Protocol

from app.core.config import settings

MAX_GROUP_SIZE = 5
MAX_GROUP_CANDIDATES = 12


class CreditLike(Protocol):
    """Что нужно сверке от поступления. Подходит и ORM-объект, и тестовый dataclass."""

    id: int
    operated_at: datetime
    amount: float


class ReceiptLike(Protocol):
    """Что нужно сверке от чека."""

    id: int
    operation_time: datetime         # дата расчёта из чека — по ней ищем пару
    request_time: datetime | None    # когда чек реально сформирован — по ней ловим поздние чеки
    amount: float


@dataclass
class Match:
    """Найденная связь: платежи, чеки, сумма, разрыв в днях и тип связи."""

    tx_ids: list[int]
    receipt_ids: list[int]
    amount: float
    days_gap: float
    kind: str  # one_to_one / many_tx / many_receipts


@dataclass
class MatchResult:
    """Результат сверки: найденные связи и всё, что осталось без пары."""

    matches: list[Match] = field(default_factory=list)
    unmatched_tx_ids: list[int] = field(default_factory=list)
    unmatched_receipt_ids: list[int] = field(default_factory=list)
    late_receipt_ids: list[int] = field(default_factory=list)  # чек сформирован позже дня оплаты


def receipt_deadline(paid_at: datetime) -> datetime:
    """Крайний срок чека: 9 число месяца, следующего за месяцем расчёта (ст. 14 422-ФЗ)."""
    first_next = (paid_at.replace(day=1) + timedelta(days=32)).replace(day=1)
    return first_next.replace(day=settings.receipt_deadline_day, hour=23, minute=59, second=59)


def in_window(paid_at: datetime, operation_time: datetime) -> bool:
    """Попадает ли дата чека в допустимое окно вокруг даты платежа."""
    earliest = paid_at - timedelta(days=settings.match_days_before)
    return earliest <= operation_time <= receipt_deadline(paid_at)


def _gap_days(a: datetime, b: datetime) -> float:
    """Разница между двумя датами в днях."""
    return abs((a - b).total_seconds()) / 86400


def _amount_ok(a: float, b: float) -> bool:
    """Совпадают ли суммы с учётом допуска."""
    return abs(a - b) <= settings.match_amount_tolerance


def match(credits: Sequence[CreditLike], receipts: Sequence[ReceiptLike]) -> MatchResult:
    """Сопоставляет поступления с чеками: сначала один к одному, потом группы. Возвращает пары и остатки."""
    result = MatchResult()
    free_tx = {c.id: c for c in credits}
    free_rc = {r.id: r for r in receipts}

    # 1. один к одному
    candidates = []
    for c in credits:
        for r in receipts:
            if _amount_ok(c.amount, r.amount) and in_window(c.operated_at, r.operation_time):
                candidates.append((_gap_days(c.operated_at, r.operation_time), abs(c.amount - r.amount), c.id, r.id))
    candidates.sort()
    for gap, _, tx_id, rc_id in candidates:
        if tx_id in free_tx and rc_id in free_rc:
            c, r = free_tx.pop(tx_id), free_rc.pop(rc_id)
            result.matches.append(Match([c.id], [r.id], r.amount, round(gap, 2), "one_to_one"))

    # 2a. несколько поступлений → один чек
    for r in sorted(free_rc.values(), key=lambda x: x.operation_time):
        pool = [c for c in free_tx.values() if in_window(c.operated_at, r.operation_time) and c.amount < r.amount]
        group = _find_subset(pool, r.amount)
        if group:
            for c in group:
                free_tx.pop(c.id)
            free_rc.pop(r.id)
            gap = max(_gap_days(c.operated_at, r.operation_time) for c in group)
            result.matches.append(Match([c.id for c in group], [r.id], r.amount, round(gap, 2), "many_tx"))

    # 2b. один платёж → несколько чеков
    for c in sorted(free_tx.values(), key=lambda x: x.operated_at):
        pool = [r for r in free_rc.values() if in_window(c.operated_at, r.operation_time) and r.amount < c.amount]
        group = _find_subset(pool, c.amount)
        if group:
            for r in group:
                free_rc.pop(r.id)
            free_tx.pop(c.id)
            gap = max(_gap_days(c.operated_at, r.operation_time) for r in group)
            result.matches.append(Match([c.id], [r.id for r in group], c.amount, round(gap, 2), "many_receipts"))

    result.unmatched_tx_ids = sorted(free_tx)
    result.unmatched_receipt_ids = sorted(free_rc)

    # Чек сформирован позже дня оплаты: для безналичных расчётов это нарушение срока, но не повод для отказа.
    # Смотрим на requestTime: operationTime самозанятый может поставить задним числом.
    by_tx = {c.id: c for c in credits}
    by_rc = {r.id: r for r in receipts}
    for m in result.matches:
        paid = max(by_tx[t].operated_at for t in m.tx_ids)
        issued = max(by_rc[r].request_time or by_rc[r].operation_time for r in m.receipt_ids)
        if issued.date() > paid.date():
            result.late_receipt_ids.extend(m.receipt_ids)
    return result


def _find_subset(pool, target: float):
    """Небольшой перебор подмножеств: в реальных кейсах групп больше 3–4 платежей почти не бывает."""
    if len(pool) < 2:
        return None
    pool = sorted(pool, key=lambda x: -x.amount)[:MAX_GROUP_CANDIDATES]
    for size in range(2, min(MAX_GROUP_SIZE, len(pool)) + 1):
        for combo in combinations(pool, size):
            if _amount_ok(sum(x.amount for x in combo), target):
                return list(combo)
    return None
