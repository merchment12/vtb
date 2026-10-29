import json
from dataclasses import dataclass
from datetime import datetime

import pytest

from app.core.validators import validate_inn
from app.models.enums import CancelReason, IncomeType
from app.services.documents.parsers import ParseError, parse_receipts, parse_statement
from app.services.review.matching import match, receipt_deadline


@dataclass
class C:
    id: int
    operated_at: datetime
    amount: float


@dataclass
class R:
    id: int
    operation_time: datetime
    amount: float
    request_time: datetime | None = None


def test_one_to_one_prefers_closest_date():
    credits = [C(1, datetime(2025, 6, 1, 10), 2000), C(2, datetime(2025, 6, 3, 10), 2000)]
    receipts = [R(10, datetime(2025, 6, 3, 10, 5), 2000), R(11, datetime(2025, 6, 1, 10, 5), 2000)]
    m = match(credits, receipts)
    pairs = {(x.tx_ids[0], x.receipt_ids[0]) for x in m.matches}
    assert pairs == {(1, 11), (2, 10)}
    assert not m.unmatched_tx_ids and not m.late_receipt_ids


def test_group_of_payments_under_one_receipt():
    credits = [C(1, datetime(2025, 6, 1, 10), 1500), C(2, datetime(2025, 6, 1, 18), 2500)]
    m = match(credits, [R(10, datetime(2025, 6, 1, 21), 4000)])
    assert m.matches[0].kind == "many_tx"
    assert sorted(m.matches[0].tx_ids) == [1, 2]


def test_one_payment_several_receipts():
    m = match([C(1, datetime(2025, 6, 1, 10), 5000)],
              [R(10, datetime(2025, 6, 1, 10), 3000), R(11, datetime(2025, 6, 1, 10), 2000)])
    assert m.matches[0].kind == "many_receipts"


def test_receipt_after_deadline_not_matched():
    m = match([C(1, datetime(2025, 6, 20), 2000)], [R(10, datetime(2025, 7, 10), 2000)])
    assert m.unmatched_tx_ids == [1] and m.unmatched_receipt_ids == [10]
    assert receipt_deadline(datetime(2025, 12, 31)).date().isoformat() == "2026-01-09"


def test_late_receipt_flagged():
    m = match([C(1, datetime(2025, 6, 20), 2000)], [R(10, datetime(2025, 7, 5), 2000)])
    assert m.late_receipt_ids == [10]


def test_backdated_receipt_flagged_by_request_time():
    """Дату расчёта поставили задним числом, но requestTime выдаёт, что чек сформирован позже."""
    m = match([C(1, datetime(2025, 6, 20, 12), 2000)],
              [R(10, datetime(2025, 6, 20, 12), 2000, request_time=datetime(2025, 6, 25, 9))])
    assert m.matches and m.late_receipt_ids == [10]


def _income(uuid, amount, **extra):
    return {"approvedReceiptUuid": uuid, "name": "Урок", "operationTime": "2025-06-03T12:00:00+03:00",
            "requestTime": "2025-06-03T12:01:00+03:00", "totalAmount": amount, **extra}


def test_lknpd_page_parsed():
    page = {"content": [
        _income("a", "2000.00", clientDisplayName="Ольга К.", incomeType="FROM_INDIVIDUAL"),
        _income("b", 30000, clientInn="7704211201", incomeType="FROM_LEGAL_ENTITY",
                cancellationInfo={"operationTime": "2025-06-04T10:00:00+03:00", "comment": "Возврат средств"}),
    ], "hasMore": False, "currentOffset": 0, "currentLimit": 100}
    a, b = parse_receipts(json.dumps(page).encode(), "incomes.json")
    assert (a.amount, a.client_name, a.income_type) == (2000.0, "Ольга К.", IncomeType.individual)
    assert a.operation_time == datetime(2025, 6, 3, 12) and a.request_time == datetime(2025, 6, 3, 12, 1)
    assert b.income_type is IncomeType.legal_entity and b.cancelled and b.cancel_reason == CancelReason.refund


def test_lknpd_incomplete_export_rejected():
    first = {"content": [_income("a", 100)], "hasMore": True, "currentOffset": 0}
    with pytest.raises(ParseError, match="hasMore"):
        parse_receipts(json.dumps(first).encode(), "incomes.json")
    gap = [first, {"content": [_income("c", 100)], "hasMore": False, "currentOffset": 5}]
    with pytest.raises(ParseError, match="пропущены"):
        parse_receipts(json.dumps(gap).encode(), "incomes.json")
    full = [first, {"content": [_income("b", 100)], "hasMore": False, "currentOffset": 1}]
    assert len(parse_receipts(json.dumps(full).encode(), "incomes.json")) == 2


def test_iso_and_russian_dates_agree():
    from app.services.documents.parsers import _to_datetime

    assert _to_datetime("2025-06-03T12:00:00+03:00") == _to_datetime("03.06.2025 12:00") == datetime(2025, 6, 3, 12)


def test_inn_checksum():
    assert validate_inn("500100732259")
    assert not validate_inn("500100732250")


def test_statement_with_income_expense_columns():
    csv = "Дата;Приход;Расход;Описание\n01.06.2025;2 000,00;;Перевод СБП\n02.06.2025;;500;Налог на профессиональный доход\n"
    rows = parse_statement(csv.encode("cp1251"), "s.csv")
    assert [(r.direction.value, r.amount, r.category.value) for r in rows] == [
        ("credit", 2000.0, "business_income"),
        ("debit", 500.0, "tax"),
    ]
