"""Формат API «Мой налог» (lknpd.nalog.ru).

API неофициальный, поля сверены с открытым клиентом github.com/shoman4eg/moy-nalog.

- POST /api/v1/income принимает operationTime, requestTime, services[{name, amount, quantity}],
  totalAmount, client{displayName, inn, incomeType, contactPhone}, paymentType и возвращает approvedReceiptUuid.
- GET /api/v1/incomes отдаёт страницу IncomeList (до 100 чеков). В ней те же поля, что в запросе,
  плюс то, что проставляет ФНС: registerTime, taxPeriodId, cancellationInfo.

Мы принимаем от клиента выгрузку GET /incomes. В таблицу receipts кладём всё, что нужно для сверки.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.models.enums import IncomeType, PaymentType


class _Lknpd(BaseModel):
    """Общая настройка: поля в camelCase, как в API, незнакомые поля игнорируются."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class ServiceItem(_Lknpd):
    """Позиция чека: услуга, количество, цена."""

    name: str
    quantity: float = 1
    amount: float
    service_number: int | None = None


class CancellationInfo(_Lknpd):
    """Данные об аннулировании чека."""

    operation_time: datetime | None = None
    register_time: datetime | None = None
    tax_period_id: int | None = None
    comment: str | None = None


class IncomeItem(_Lknpd):
    """Один чек из ответа GET /api/v1/incomes."""

    approved_receipt_uuid: str
    name: str | None = None
    services: list[ServiceItem] = []
    operation_time: datetime                 # дата расчёта: самозанятый указывает её сам, можно задним числом
    request_time: datetime | None = None     # когда чек реально сформирован
    register_time: datetime | None = None    # когда ФНС зарегистрировала чек
    tax_period_id: int | None = None         # налоговый период, например 202506
    payment_type: PaymentType | None = None
    income_type: IncomeType | None = None
    total_amount: float
    cancellation_info: CancellationInfo | None = None
    client_inn: str | None = None
    client_display_name: str | None = None
    partner_code: str | None = None          # чек сформирован через партнёра «Мой налог» (банк, платформа)
    partner_display_name: str | None = None
    partner_inn: str | None = None
    inn: str | None = None                   # ИНН самозанятого, выбившего чек
    profession: str | None = None


class IncomeList(_Lknpd):
    """Страница ответа GET /api/v1/incomes: до 100 чеков и признак, есть ли следующая."""

    content: list[IncomeItem]
    has_more: bool = False
    current_offset: int = 0
    current_limit: int | None = None
