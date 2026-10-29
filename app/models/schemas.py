"""Схемы запросов и ответов API (Pydantic)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.validators import validate_inn
from app.models.enums import BlockReason, CaseStatus, DocType, TxCategory, TxDirection


class CaseCreate(BaseModel):
    """Тело POST /api/cases: данные клиента и блокировки."""

    inn: str = Field(examples=["500100732259"])  # ИНН физлица, 12 цифр
    full_name: str = Field(examples=["Иванова Анна Сергеевна"])
    phone: str | None = None
    activity: str | None = Field(None, examples=["Репетитор по английскому"])  # чем зарабатывает
    block_reason: BlockReason  # по какому основанию банк запросил документы
    blocked_at: date | None = None  # от этой даты отсчитываются 90 дней выписки

    @field_validator("inn")
    @classmethod
    def check_inn(cls, v: str) -> str:
        """Проверяет ИНН физлица по контрольным цифрам."""
        v = v.strip()
        if not validate_inn(v):
            raise ValueError("Некорректный ИНН физлица (12 цифр, проверка контрольных разрядов)")
        return v


class DocumentOut(BaseModel):
    """Документ в ответе API (без пути к файлу на сервере)."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    doc_type: DocType
    filename: str
    uploaded_at: datetime


class CaseOut(BaseModel):
    """Кейс в ответе API."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    inn: str
    full_name: str
    activity: str | None
    block_reason: BlockReason
    blocked_at: date | None
    status: CaseStatus
    npd_status: str | None  # статус в ФНС: active / inactive / unknown
    request_rounds: int  # сколько раз сотрудник запрашивал документы повторно
    created_at: datetime
    submitted_at: datetime | None  # когда клиент отправил полный пакет
    decided_at: datetime | None  # когда сотрудник принял решение
    documents: list[DocumentOut]


class TxOut(BaseModel):
    """Операция из выписки в ответе API."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    operated_at: datetime
    amount: float  # всегда положительная, знак — в direction
    direction: TxDirection
    counterparty: str | None  # отправитель или получатель
    purpose: str | None  # назначение платежа
    category: TxCategory
    client_comment: str | None  # пояснение клиента


class TxUpdate(BaseModel):
    """Пояснение клиента к операции. Категорию можно сменить только на допустимые для поступлений."""

    category: Literal["business_income", "personal", "own_transfer", "refund"] | None = None
    client_comment: str | None = Field(None, max_length=1000, examples=["Возврат долга от брата"])


class UploadResult(BaseModel):
    """Ответ на загрузку документа."""

    document: DocumentOut
    parsed: int  # сколько операций или чеков разобрано из файла (для справок — 0)
    ready_to_submit: bool  # закрыты ли все обязательные пункты чек-листа


class DocsRequest(BaseModel):
    """Тело запроса документов от сотрудника."""

    items: list[str] | None = Field(None, description="Пусто — взять черновик, собранный системой")
    comment: str | None = None  # свободный текст для клиента


class DecisionIn(BaseModel):
    """Решение сотрудника по кейсу."""

    decision: Literal["unblock", "reject"]
    comment: str = Field(min_length=3)  # обоснование обязательно: оно уходит в журнал


class EventOut(BaseModel):
    """Событие из журнала кейса."""

    model_config = ConfigDict(from_attributes=True)
    actor: str  # client / staff / system
    kind: str  # created, uploaded, submitted, docs_requested, decided...
    payload: dict | None  # подробности события
    created_at: datetime
