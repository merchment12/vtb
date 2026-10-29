"""Перечисления, общие для таблиц БД и схем API."""

import enum


class CaseStatus(str, enum.Enum):
    """Статус кейса на пути от черновика клиента до решения сотрудника."""

    draft = "draft"                    # клиент собирает пакет
    submitted = "submitted"            # пакет полный, ждёт сотрудника
    in_review = "in_review"
    docs_requested = "docs_requested"  # сотрудник запросил дополнительно
    unblocked = "unblocked"
    rejected = "rejected"


class BlockReason(str, enum.Enum):
    """Основание запроса банка по 115-ФЗ. От него зависит набор обязательных документов."""

    income_source = "income_source"    # подтвердить источник поступлений
    transit = "transit"                # транзитный характер операций
    cash_out = "cash_out"              # активное снятие наличных
    p2p_volume = "p2p_volume"          # много переводов от физлиц


class TxDirection(str, enum.Enum):
    """Направление операции в выписке."""

    credit = "credit"
    debit = "debit"


class TxCategory(str, enum.Enum):
    """Категория операции. Ставится автоматически при разборе выписки, клиент может поправить."""

    business_income = "business_income"  # доход от деятельности, нужен чек
    own_transfer = "own_transfer"        # между своими счетами
    personal = "personal"                # подарок, долг, от родственника — нужен комментарий
    refund = "refund"                    # возврат, кешбэк, проценты
    tax = "tax"                          # уплата НПД
    cash = "cash"                        # снятие наличных
    transfer_out = "transfer_out"        # перевод другому лицу
    purchase = "purchase"                # оплата покупок/услуг
    other = "other"


class DocType(str, enum.Enum):
    """Тип документа, который загружает клиент. Операций по карте здесь нет: их банк берёт из своей системы."""

    receipts = "receipts"
    npd_certificate = "npd_certificate"  # справка о постановке на учёт, КНД 1122035
    income_certificate = "income_certificate"  # справка о доходах НПД, КНД 1122036
    contract = "contract"
    act = "act"
    explanation = "explanation"
    other = "other"


# --- значения из API «Мой налог» (lknpd.nalog.ru), см. app/models/lknpd.py ---

class IncomeType(str, enum.Enum):
    """incomeType — от кого доход. От этого зависит ставка НПД: 4% с физлиц, 6% с юрлиц и ИП."""

    individual = "FROM_INDIVIDUAL"
    legal_entity = "FROM_LEGAL_ENTITY"
    foreign_agency = "FROM_FOREIGN_AGENCY"


class PaymentType(str, enum.Enum):
    """paymentType чека."""

    cash = "CASH"
    account = "ACCOUNT"


class CancelReason(str, enum.Enum):
    """cancellationInfo.comment — «Мой налог» принимает при аннулировании только эти два варианта."""

    mistake = "Чек сформирован ошибочно"
    refund = "Возврат средств"
