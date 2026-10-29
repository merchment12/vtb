"""Приём документа от клиента: разбор чеков, сохранение файла на диск, замена прошлой версии."""

from pathlib import Path

from app.core.config import settings
from app.models.enums import DocType
from app.models.orm import Case, Document, Receipt, utcnow
from app.services.documents.parsers import parse_receipts

# чеки в кейсе существуют в одном экземпляре, новая загрузка заменяет старую
SINGLE_DOCS = (DocType.receipts,)


def attach_document(case: Case, doc_type: DocType, filename: str, content: bytes) -> tuple[Document, int]:
    """Возвращает документ и число разобранных строк. Бросает ParseError на кривом файле."""
    parsed = 0
    if doc_type is DocType.receipts:
        rows = parse_receipts(content, filename)
        case.receipts.clear()
        case.receipts.extend(Receipt(**vars(r)) for r in rows)
        parsed = len(rows)

    path = _save(case.id, doc_type, filename, content)
    if doc_type in SINGLE_DOCS:
        case.documents[:] = [d for d in case.documents if d.doc_type is not doc_type]
    doc = Document(doc_type=doc_type, filename=filename, path=str(path))
    case.documents.append(doc)
    return doc, parsed


def _save(case_id: int, doc_type: DocType, filename: str, content: bytes) -> Path:
    """Сохраняет файл в upload_dir/<id кейса>/, в имени — тип документа и время загрузки."""
    folder = Path(settings.upload_dir) / str(case_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{doc_type.value}_{utcnow():%Y%m%d%H%M%S}_{filename}"
    path.write_bytes(content)
    return path
