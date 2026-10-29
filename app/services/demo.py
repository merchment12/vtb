"""Демо-кейсы для показа: три готовых пакета в очереди сотрудника одним вызовом.

1. Честный репетитор — доход подтверждён чеками, одно поступление пояснено как личное.
2. Дроппер — транзит, мало чеков, превышение лимита НПД.
3. Скрытый доход — та же выписка, что у репетитора, но клиент отнёс к «личным» больше половины поступлений,
   чтобы не показывать их чеками. Ловится индикатором «Поступления, отнесённые клиентом к личным».

Все данные синтетические (scripts/generate_samples.py).
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import repository
from app.models.enums import BlockReason, CaseStatus, DocType, TxCategory, TxDirection
from app.models.orm import Case, utcnow
from app.services.bank import core
from app.services.bank.transactions import pull_transactions
from app.services.documents.upload import attach_document
from app.services.review.report import build_report

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
STUB_PDF = b"%PDF-1.4 demo stub"


def _samples() -> dict:
    """ИНН демо-клиентов. Если семплов ещё нет — генерирует их."""
    if not (SAMPLES / "README.json").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_samples.py")], check=True)
    return json.loads((SAMPLES / "README.json").read_text("utf-8"))


def _make_case(db: Session, inn: str, name: str, activity: str, reason: BlockReason, blocked_at: date,
               statement: str, receipts: str, extra_docs: list[DocType]) -> Case:
    """Создаёт кейс так же, как клиентский сценарий: операции из банка, чеки и справки от клиента."""
    core.store_statement(inn, statement, (SAMPLES / statement).read_bytes())
    case = Case(inn=inn, full_name=name, activity=activity, block_reason=reason, blocked_at=blocked_at,
                npd_status="unknown")
    db.add(case)
    db.flush()
    repository.log_event(db, case, "client", "created", {"block_reason": reason.value, "demo": True})
    pull_transactions(case)
    attach_document(case, DocType.receipts, receipts, (SAMPLES / receipts).read_bytes())
    for doc in extra_docs:
        attach_document(case, doc, f"{doc.value}.pdf", STUB_PDF)
    return case


def _explain_unmatched(case: Case, comment: str, personal_amount: float | None = None) -> None:
    """Пояснения клиента к поступлениям без чека, как в приложении."""
    report = build_report(case)
    by_id = {t.id: t for t in case.transactions}
    for item in report["matching"]["unmatched_income"]:
        tx = by_id[item["id"]]
        if personal_amount is not None and tx.amount == personal_amount:
            tx.category, tx.client_comment = TxCategory.personal, "Перевод от мамы на день рождения"
        else:
            tx.client_comment = comment


def _submit(db: Session, case: Case) -> None:
    """Отправка полного пакета сотруднику."""
    if not build_report(case)["ready_to_submit"]:
        raise RuntimeError(f"Демо-кейс {case.id} не готов к отправке")
    case.status = CaseStatus.submitted
    case.submitted_at = utcnow()
    repository.log_event(db, case, "client", "submitted", {"round": 0, "demo": True})


def seed(db: Session) -> list[int]:
    """Создаёт три демо-кейса в статусе «отправлен» и возвращает их id."""
    s = _samples()
    blocked = date.fromisoformat(s["blocked_at"])
    ids = []

    honest = _make_case(db, s["honest_inn"], "Анна Смирнова (демо)", "Репетитор по математике",
                        BlockReason.income_source, blocked, "honest_statement.csv", "honest_receipts.json",
                        [DocType.npd_certificate, DocType.income_certificate])
    _explain_unmatched(honest, "Оплата занятия, чек выбит позже — пропустила", personal_amount=15000)
    _submit(db, honest)
    ids.append(honest.id)

    dropper = _make_case(db, s["dropper_inn"], "Игорь Петров (демо)", "Не указано",
                         BlockReason.transit, blocked, "dropper_statement.csv", "dropper_receipts.csv",
                         [DocType.npd_certificate, DocType.explanation])
    _explain_unmatched(dropper, "Оплата услуг")
    _submit(db, dropper)
    ids.append(dropper.id)

    hider = _make_case(db, s["honest_inn"], "Олег Кузнецов (демо)", "Репетитор по физике",
                       BlockReason.income_source, blocked, "honest_statement.csv", "honest_receipts.json",
                       [DocType.npd_certificate, DocType.income_certificate])
    income = sorted((t for t in hider.transactions
                     if t.direction is TxDirection.credit and t.category is TxCategory.business_income),
                    key=lambda t: t.operated_at)
    for t in income[::5] + income[1::5] + income[2::5]:  # 60% поступлений объявлены «личными»
        t.category, t.client_comment = TxCategory.personal, "Подарок от знакомого"
    hider.receipts[:] = [r for i, r in enumerate(sorted(hider.receipts, key=lambda r: r.operation_time)) if i % 5 > 2]
    _explain_unmatched(hider, "Оплата занятия")
    _submit(db, hider)
    ids.append(hider.id)

    db.commit()
    return ids
