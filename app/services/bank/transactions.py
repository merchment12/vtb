"""Загрузка операций по карте из банка в кейс."""

from datetime import date

from app.models.orm import Case, Transaction
from app.services.bank import core


def pull_transactions(case: Case) -> int:
    """Подтягивает операции клиента из банка в кейс. Возвращает их число.

    Пояснения и категории, которые клиент уже проставил, сохраняются: операция узнаётся
    по времени, сумме, направлению и контрагенту.
    """
    edits = {_key(t): (t.category, t.client_comment) for t in case.transactions}
    rows = core.fetch_transactions(case.inn, case.blocked_at or date.today())
    case.transactions.clear()
    for row in rows:
        tx = Transaction(**vars(row))
        tx.auto_category = tx.category  # исходная категория нужна, чтобы видеть, что клиент перекатегоризировал
        if saved := edits.get(_key(tx)):
            tx.category, tx.client_comment = saved
        case.transactions.append(tx)
    return len(rows)


def _key(t: Transaction) -> tuple:
    """Признаки, по которым одна и та же операция узнаётся при повторной загрузке."""
    return t.operated_at, round(t.amount, 2), t.direction, t.counterparty
