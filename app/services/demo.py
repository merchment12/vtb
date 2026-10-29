"""Демо-кейсы для показа: три готовых пакета в очереди сотрудника одним вызовом.

1. Честный репетитор — доход подтверждён чеками, одно поступление пояснено как личное.
2. Дроппер — транзит, мало чеков, превышение лимита НПД.
3. Скрытый доход — та же выписка, что у репетитора, но клиент отнёс к «личным» больше половины поступлений,
   чтобы не показывать их чеками. Ловится индикатором «Поступления, отнесённые клиентом к личным».

Все данные синтетические (scripts/generate_samples.py).
"""

import json
import random
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import repository
from app.models.enums import BlockReason, CaseStatus, DocType, TxCategory, TxDirection
from app.models.orm import Case, utcnow
from app.services.bank import core
from app.services.bank.transactions import pull_transactions
from app.services.documents.parsers import ParseError
from app.services.documents.upload import attach_document
from app.services.review.checklist import DOC_TITLES, REQUIRED_DOCS
from app.services.review.report import build_report

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
# подписи в демо-PDF латиницей: стандартные шрифты PDF не умеют кириллицу без встраивания
PDF_TITLES = {
    DocType.npd_certificate: "NPD registration certificate (KND 1122035)",
    DocType.income_certificate: "NPD income certificate (KND 1122036)",
    DocType.explanation: "Written explanation of account operations",
    DocType.contract: "Contract with a major customer",
    DocType.act: "Certificate of completed work",
}


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
        attach_document(case, doc, *_demo_file(case, doc))
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


def clients() -> list[dict]:
    """Демо-клиенты, по которым в эмуляторе банка есть операции: для быстрого заполнения формы."""
    s = _samples()
    for inn, statement in ((s["honest_inn"], "honest_statement.csv"), (s["dropper_inn"], "dropper_statement.csv")):
        if not any(Path(settings.bank_core_dir).glob(f"{inn}.*")):
            core.store_statement(inn, statement, (SAMPLES / statement).read_bytes())
    return [
        {"label": "Честный репетитор", "inn": s["honest_inn"], "full_name": "Мария Иванова",
         "activity": "Репетитор по английскому", "block_reason": BlockReason.income_source.value,
         "blocked_at": s["blocked_at"]},
        {"label": "Дроппер", "inn": s["dropper_inn"], "full_name": "Сергей Орлов",
         "activity": "Консультации", "block_reason": BlockReason.transit.value, "blocked_at": s["blocked_at"]},
    ]


def fill_documents(db: Session, case: Case) -> dict:
    """Загружает за клиента всё обязательное для его основания блокировки, что ещё не загружено.

    Файлы идут тем же путём, что ручная загрузка: чеки разбираются, всё попадает в чек-лист и переписку.
    """
    missing = REQUIRED_DOCS[case.block_reason] - {d.doc_type for d in case.documents}
    added, receipts = [], 0
    for doc_type in sorted(missing, key=lambda d: d is not DocType.receipts):  # чеки первыми
        _, parsed = attach_document(case, doc_type, *_demo_file(case, doc_type))
        repository.log_event(db, case, "client", "uploaded", {"doc_type": doc_type.value, "parsed": parsed, "demo": True})
        added.append(DOC_TITLES[doc_type])
        receipts += parsed
    return {"uploaded": added, "receipts": receipts}


def _demo_file(case: Case, doc_type: DocType) -> tuple[str, bytes]:
    """Имя и содержимое демо-файла: чеки демо-клиента из samples/ или синтетика, для справок — PDF."""
    if doc_type is DocType.receipts:
        s = _samples()
        sample = {s["honest_inn"]: "honest_receipts.json", s["dropper_inn"]: "dropper_receipts.csv"}.get(case.inn)
        if sample:
            return sample, (SAMPLES / sample).read_bytes()
        return "incomes_demo.json", _synthetic_receipts(case)
    title = PDF_TITLES.get(doc_type, doc_type.value)
    return f"{doc_type.value}_demo.pdf", _demo_pdf([f"DEMO: {title}", f"INN {case.inn}", "Synthetic document for the MVP demo."])


def _synthetic_receipts(case: Case) -> bytes:
    """Чеки в формате GET /api/v1/incomes «Мой налог» по поступлениям клиента из банка.

    Примерно на 85% поступлений есть чек, остальные остаются клиенту для пояснения — как у обычного самозанятого.
    """
    rng = random.Random(case.id)
    income = sorted((t for t in case.transactions
                     if t.direction is TxDirection.credit and t.category is TxCategory.business_income),
                    key=lambda t: t.operated_at)
    if not income:
        raise ParseError("Из банка не пришло поступлений — не по чему сделать демо-чеки")
    content = []
    for i, t in enumerate(income):
        if rng.random() > 0.85:
            continue
        when = (t.operated_at + timedelta(minutes=rng.randint(1, 30))).strftime("%Y-%m-%dT%H:%M:%S+03:00")
        content.append({
            "approvedReceiptUuid": f"demo{case.id:04d}{i:06d}", "name": case.activity or "Услуги",
            "operationTime": when, "requestTime": when, "totalAmount": t.amount,
            "incomeType": "FROM_INDIVIDUAL", "paymentType": "CASH",
            "clientDisplayName": t.counterparty, "inn": case.inn, "cancellationInfo": None,
        })
    page = {"content": content, "hasMore": False, "currentOffset": 0, "currentLimit": len(content)}
    return json.dumps(page, ensure_ascii=False).encode()


def _demo_pdf(lines: list[str]) -> bytes:
    """Минимальный настоящий PDF на одну страницу: открывается в любом просмотрщике."""
    text = " ".join(f"({line}) Tj 0 -22 Td" for line in lines)
    stream = f"BT /F1 14 Tf 72 760 Td {text} ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
