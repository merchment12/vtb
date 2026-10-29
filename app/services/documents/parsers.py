"""Разбор выписки (CSV/XLSX) и чеков «Мой налог» (JSON/CSV/XLSX).

Колонки ищутся по синонимам, чтобы принимать разные выгрузки без ручной подготовки.
"""

import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
from pydantic import ValidationError

from app.models.enums import IncomeType, PaymentType, TxCategory, TxDirection
from app.models.lknpd import IncomeItem, IncomeList


class ParseError(ValueError):
    """Файл не удалось разобрать. Текст ошибки показывается клиенту."""

    pass


@dataclass
class ParsedTx:
    """Операция из выписки до записи в БД. Поля совпадают с таблицей transactions."""

    operated_at: datetime
    amount: float
    direction: TxDirection
    counterparty: str | None
    purpose: str | None
    category: TxCategory


@dataclass
class ParsedReceipt:
    """Чек до записи в БД. Поля совпадают с таблицей receipts."""

    external_id: str | None
    operation_time: datetime
    amount: float
    service: str | None
    client_name: str | None
    client_inn: str | None
    seller_inn: str | None
    cancelled: bool
    request_time: datetime | None = None
    tax_period_id: int | None = None
    services: list[dict] | None = None
    payment_type: PaymentType | None = None
    income_type: IncomeType | None = None
    partner_name: str | None = None
    partner_inn: str | None = None
    cancel_reason: str | None = None
    cancelled_at: datetime | None = None


STATEMENT_ALIASES = {
    "date": ["дата операции", "дата и время", "дата", "date", "operated_at", "datetime"],
    "time": ["время", "time"],
    "amount": ["сумма операции", "сумма", "amount"],
    "credit": ["приход", "поступление", "зачисление", "credit"],
    "debit": ["расход", "списание", "debit"],
    "direction": ["тип операции", "направление", "тип", "direction"],
    "counterparty": ["контрагент", "отправитель/получатель", "плательщик/получатель", "плательщик", "counterparty"],
    "purpose": ["назначение платежа", "назначение", "описание", "комментарий", "purpose", "description"],
}

RECEIPT_ALIASES = {
    "date": ["дата формирования", "дата чека", "дата", "date", "operationtime", "operation_time"],
    "amount": ["сумма", "amount", "totalamount"],
    "service": ["наименование", "услуга", "service", "name"],
    "client_name": ["покупатель", "клиент", "client", "client_name"],
    "client_inn": ["инн покупателя", "client_inn"],
    "seller_inn": ["инн продавца", "инн", "seller_inn", "inn"],
    "status": ["статус", "status"],
    "id": ["номер чека", "id чека", "uuid", "id", "approvedreceiptuuid"],
}

MSK = timezone(timedelta(hours=3))
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
CREDIT_WORDS = ("приход", "зачисл", "поступ", "credit", "in")
OWN_TRANSFER_RE = re.compile(r"между своими|собственных средств|своего сч[её]та|own transfer", re.I)
REFUND_RE = re.compile(r"кешб[эе]к|cashback|возврат|начислени[ея] процент|проценты по", re.I)
TAX_RE = re.compile(r"налог на профессиональный доход|\bнпд\b|уфк|фнс", re.I)
CASH_RE = re.compile(r"сняти[ея] наличн|банкомат|\batm\b|выдача наличн", re.I)
TRANSFER_RE = re.compile(r"перевод|сбп|\bp2p\b", re.I)
PURCHASE_RE = re.compile(r"оплата|покупка|\bpos\b", re.I)


def _read_table(content: bytes, filename: str) -> pd.DataFrame:
    """Читает CSV или XLSX в DataFrame, названия колонок — в нижнем регистре."""
    name = filename.lower()
    try:
        if name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        elif name.endswith(".csv"):
            text = _decode(content)
            df = pd.read_csv(io.StringIO(text), sep=None, engine="python")
        else:
            raise ParseError(f"Неподдерживаемый формат файла: {filename}. Нужен CSV или XLSX")
    except ParseError:
        raise
    except Exception as exc:  # pandas бросает много разных исключений на кривых файлах
        raise ParseError(f"Не удалось прочитать таблицу: {exc}") from exc
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df.dropna(how="all")


def _decode(content: bytes) -> str:
    """Декодирует текст файла: сначала UTF-8, потом Windows-1251."""
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParseError("Не удалось определить кодировку файла (ожидается UTF-8 или Windows-1251)")


def _find(df: pd.DataFrame, aliases: list[str]) -> str | None:
    """Ищет колонку по списку синонимов. Возвращает первую найденную или None."""
    for alias in aliases:
        if alias in df.columns:
            return alias
    return None


def _to_float(value) -> float | None:
    """Число из ячейки: понимает «2 000,00», «-500», «+1.5». Пусто — None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d,.\-+]", "", str(value)).replace(",", ".")
    if text in ("", "-", "+"):
        return None
    return float(text)


def _to_datetime(value, time_value=None) -> datetime:
    """Дата из ячейки (можно с отдельной колонкой времени) в московском времени без пояса."""
    if time_value is not None and not pd.isna(time_value):
        value = f"{value} {time_value}"
    # dayfirst ломает ISO-даты из «Мой налог» (2025-06-03 → 6 марта), поэтому только для 03.06.2025
    iso = isinstance(value, str) and ISO_DATE_RE.match(value.strip())
    ts = pd.to_datetime(value, dayfirst=not iso, errors="coerce")
    if pd.isna(ts):
        raise ParseError(f"Не удалось разобрать дату: {value!r}")
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Europe/Moscow").tz_localize(None)
    return ts.to_pydatetime()


def _clean(value) -> str | None:
    """Строка без пробелов по краям. Пусто — None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def categorize(direction: TxDirection, purpose: str | None, counterparty: str | None) -> TxCategory:
    """Категория операции по назначению платежа и контрагенту."""
    text = f"{purpose or ''} {counterparty or ''}"
    if OWN_TRANSFER_RE.search(text):
        return TxCategory.own_transfer
    if direction is TxDirection.credit:
        if REFUND_RE.search(text):
            return TxCategory.refund
        return TxCategory.business_income
    if TAX_RE.search(text):
        return TxCategory.tax
    if CASH_RE.search(text):
        return TxCategory.cash
    if TRANSFER_RE.search(text):
        return TxCategory.transfer_out
    if PURCHASE_RE.search(text):
        return TxCategory.purchase
    return TxCategory.other


def parse_statement(content: bytes, filename: str) -> list[ParsedTx]:
    """Разбирает выписку: дата, сумма, направление, контрагент, назначение и категория операции."""
    df = _read_table(content, filename)
    cols = {key: _find(df, aliases) for key, aliases in STATEMENT_ALIASES.items()}
    if not cols["date"]:
        raise ParseError("В выписке не найдена колонка с датой операции")
    if not cols["amount"] and not (cols["credit"] or cols["debit"]):
        raise ParseError("В выписке не найдена колонка с суммой (или «Приход»/«Расход»)")

    result: list[ParsedTx] = []
    for _, row in df.iterrows():
        operated_at = _to_datetime(row[cols["date"]], row[cols["time"]] if cols["time"] else None)
        if cols["amount"]:
            amount = _to_float(row[cols["amount"]])
            if amount is None:
                continue
            if cols["direction"]:
                raw_dir = str(row[cols["direction"]]).strip().lower()
                direction = TxDirection.credit if raw_dir.startswith(CREDIT_WORDS) else TxDirection.debit
            else:
                direction = TxDirection.credit if amount > 0 else TxDirection.debit
        else:
            credit = _to_float(row[cols["credit"]]) if cols["credit"] else None
            debit = _to_float(row[cols["debit"]]) if cols["debit"] else None
            if credit:
                amount, direction = credit, TxDirection.credit
            elif debit:
                amount, direction = debit, TxDirection.debit
            else:
                continue
        amount = abs(amount)
        if amount == 0:
            continue
        counterparty = _clean(row[cols["counterparty"]]) if cols["counterparty"] else None
        purpose = _clean(row[cols["purpose"]]) if cols["purpose"] else None
        result.append(
            ParsedTx(
                operated_at=operated_at,
                amount=round(amount, 2),
                direction=direction,
                counterparty=counterparty,
                purpose=purpose,
                category=categorize(direction, purpose, counterparty),
            )
        )
    if not result:
        raise ParseError("В выписке не найдено ни одной операции")
    return result


def parse_receipts(content: bytes, filename: str) -> list[ParsedReceipt]:
    """Разбирает чеки: JSON из «Мой налог» или таблицу CSV/XLSX."""
    if filename.lower().endswith(".json"):
        return _parse_receipts_json(content)
    df = _read_table(content, filename)
    cols = {key: _find(df, aliases) for key, aliases in RECEIPT_ALIASES.items()}
    if not cols["date"] or not cols["amount"]:
        raise ParseError("В файле чеков нужны как минимум колонки «Дата» и «Сумма»")

    result = []
    for _, row in df.iterrows():
        amount = _to_float(row[cols["amount"]])
        if amount is None:
            continue
        status = str(row[cols["status"]]).lower() if cols["status"] else ""
        result.append(
            ParsedReceipt(
                external_id=_clean(row[cols["id"]]) if cols["id"] else None,
                operation_time=_to_datetime(row[cols["date"]]),
                amount=round(abs(amount), 2),
                service=_clean(row[cols["service"]]) if cols["service"] else None,
                client_name=_clean(row[cols["client_name"]]) if cols["client_name"] else None,
                client_inn=_clean(row[cols["client_inn"]]) if cols["client_inn"] else None,
                seller_inn=_clean(row[cols["seller_inn"]]) if cols["seller_inn"] else None,
                cancelled="аннулир" in status or "cancel" in status,
            )
        )
    if not result:
        raise ParseError("В файле не найдено ни одного чека")
    return result


def _parse_receipts_json(content: bytes) -> list[ParsedReceipt]:
    """Выгрузка GET /api/v1/incomes «Мой налог»: одна страница, список страниц или просто список чеков."""
    try:
        data = json.loads(_decode(content))
    except json.JSONDecodeError as exc:
        raise ParseError(f"Некорректный JSON: {exc}") from exc
    if isinstance(data, dict):
        raw_pages = [data]
    elif isinstance(data, list) and data and all(isinstance(x, dict) and "content" in x for x in data):
        raw_pages = data
    else:
        raw_pages = [{"content": data}]
    try:
        pages = sorted((IncomeList.model_validate(p) for p in raw_pages), key=lambda p: p.current_offset)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(x) for x in err["loc"])
        raise ParseError(f"Файл не похож на выгрузку чеков «Мой налог»: {where} — {err['msg']}") from exc

    # неполная выгрузка — ровно та ситуация, из-за которой банк потом шлёт повторный запрос
    offset = 0
    for page in pages:
        if page.current_offset != offset:
            raise ParseError(f"В выгрузке пропущены чеки с {offset + 1} по {page.current_offset}. Выгрузите все страницы.")
        offset += len(page.content)
    if pages and pages[-1].has_more:
        raise ParseError("Выгрузка неполная: «Мой налог» вернул hasMore=true. Выгрузите все страницы чеков за период.")

    result = [_from_income(item) for page in pages for item in page.content]
    if not result:
        raise ParseError("В JSON не найдено ни одного чека")
    return result


def _from_income(item: IncomeItem) -> ParsedReceipt:
    """Переводит чек из формата «Мой налог» в ParsedReceipt."""
    cancel = item.cancellation_info
    return ParsedReceipt(
        external_id=item.approved_receipt_uuid,
        operation_time=_msk(item.operation_time),
        request_time=_msk(item.request_time),
        tax_period_id=item.tax_period_id,
        amount=round(abs(item.total_amount), 2),
        service=item.name or ", ".join(s.name for s in item.services) or None,
        services=[s.model_dump() for s in item.services] or None,
        payment_type=item.payment_type,
        income_type=item.income_type,
        client_name=item.client_display_name,
        client_inn=item.client_inn,
        seller_inn=item.inn,
        partner_name=item.partner_display_name,
        partner_inn=item.partner_inn,
        cancelled=cancel is not None,
        cancel_reason=cancel.comment if cancel else None,
        cancelled_at=_msk(cancel.operation_time) if cancel else None,
    )


def _msk(value: datetime | None) -> datetime | None:
    """Время в БД хранится без пояса, по Москве — как в выписке."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(MSK).replace(tzinfo=None)
