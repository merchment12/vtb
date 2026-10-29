"""Таблицы БД (SQLAlchemy)."""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import BlockReason, CaseStatus, DocType, IncomeType, PaymentType, TxCategory, TxDirection


def utcnow() -> datetime:
    """Текущее время в UTC — значение по умолчанию для дат создания."""
    return datetime.now(timezone.utc)


class Case(Base):
    """Кейс разблокировки: один клиент, одна блокировка, весь пакет документов и история."""

    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    inn: Mapped[str] = mapped_column(String(12), index=True)  # ИНН самозанятого
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    activity: Mapped[str | None] = mapped_column(String(255))  # чем занимается: репетитор, дизайнер...
    block_reason: Mapped[BlockReason] = mapped_column(Enum(BlockReason))  # основание запроса по 115-ФЗ
    blocked_at: Mapped[date | None] = mapped_column(Date)  # дата блокировки
    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus), default=CaseStatus.draft)
    npd_status: Mapped[str | None] = mapped_column(String(32))  # active / inactive / unknown
    request_rounds: Mapped[int] = mapped_column(Integer, default=0)  # сколько раз запрашивали документы повторно
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # первая отправка полного пакета
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # решение сотрудника

    documents: Mapped[list["Document"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    receipts: Mapped[list["Receipt"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    events: Mapped[list["CaseEvent"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="CaseEvent.created_at"
    )


class Document(Base):
    """Загруженный файл. Сам файл лежит на диске в upload_dir, здесь его тип и путь."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    doc_type: Mapped[DocType] = mapped_column(Enum(DocType))
    filename: Mapped[str] = mapped_column(String(255))  # имя файла, как его загрузил клиент
    path: Mapped[str] = mapped_column(String(512))  # где файл лежит на диске
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="documents")


class Transaction(Base):
    """Операция из выписки клиента."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    operated_at: Mapped[datetime] = mapped_column(DateTime)  # дата и время операции, по Москве
    amount: Mapped[float] = mapped_column(Float)  # всегда положительная, знак — в direction
    direction: Mapped[TxDirection] = mapped_column(Enum(TxDirection))
    counterparty: Mapped[str | None] = mapped_column(String(255))  # отправитель или получатель
    purpose: Mapped[str | None] = mapped_column(Text)  # назначение платежа
    category: Mapped[TxCategory] = mapped_column(Enum(TxCategory))  # автоматически, клиент может поменять
    auto_category: Mapped[TxCategory | None] = mapped_column(Enum(TxCategory))  # как определила система; клиент не меняет
    client_comment: Mapped[str | None] = mapped_column(Text)  # пояснение клиента к операции

    case: Mapped[Case] = relationship(back_populates="transactions")


class Receipt(Base):
    """Чек «Мой налог». Поля повторяют ответ GET /api/v1/incomes (app/models/lknpd.py)."""

    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(64))  # approvedReceiptUuid
    operation_time: Mapped[datetime] = mapped_column(DateTime)  # дата расчёта, её указывает сам самозанятый
    request_time: Mapped[datetime | None] = mapped_column(DateTime)  # когда чек реально сформирован
    tax_period_id: Mapped[int | None] = mapped_column(Integer)  # например 202506
    amount: Mapped[float] = mapped_column(Float)  # totalAmount
    service: Mapped[str | None] = mapped_column(String(512))  # name
    services: Mapped[list | None] = mapped_column(JSON)  # services[]: name, quantity, amount
    payment_type: Mapped[PaymentType | None] = mapped_column(Enum(PaymentType))
    income_type: Mapped[IncomeType | None] = mapped_column(Enum(IncomeType))
    client_name: Mapped[str | None] = mapped_column(String(255))  # clientDisplayName
    client_inn: Mapped[str | None] = mapped_column(String(12))
    seller_inn: Mapped[str | None] = mapped_column(String(12))  # inn
    partner_name: Mapped[str | None] = mapped_column(String(255))  # partnerDisplayName
    partner_inn: Mapped[str | None] = mapped_column(String(12))
    cancelled: Mapped[bool] = mapped_column(default=False)
    cancel_reason: Mapped[str | None] = mapped_column(String(255))  # cancellationInfo.comment
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)

    case: Mapped[Case] = relationship(back_populates="receipts")


class CaseEvent(Base):
    """Журнал по кейсу: из него считаются метрики для финмодели (время разбора, число кругов запросов)."""

    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    actor: Mapped[str] = mapped_column(String(16))  # client / staff / system
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="events")
