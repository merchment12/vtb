"""Экраны клиента и сотрудника, переписка и пересоздание демо-базы при смене схемы."""

import sqlite3

from tests.test_flow import _create, _load_bank, _upload_stub


def test_pages_served(client):
    for path in ("/", "/client", "/staff", "/static/common.js", "/static/common.css"):
        assert client.get(path).status_code == 200, path


def test_chat_and_decision_visibility(client, samples):
    _load_bank(client, samples["honest_inn"], "honest_statement.csv")
    cid = _create(client, samples["honest_inn"])
    r = client.post(f"/api/cases/{cid}/messages", json={"text": "Справку о доходах донесу завтра"})
    last = r.json()[-1]
    assert r.status_code == 201 and (last["from"], last["text"]) == ("client", "Справку о доходах донесу завтра")
    client.post(f"/api/staff/cases/{cid}/messages", json={"text": "Хорошо, ждём"})

    # решение: клиент видит итог, но не внутреннее обоснование сотрудника
    case = client.get(f"/api/cases/{cid}").json()
    assert case["status"] == "draft"
    _upload_stub(client, cid, "npd_certificate")
    from app.db.session import SessionLocal
    from app.models.enums import CaseStatus
    from app.models.orm import Case
    with SessionLocal() as db:  # пропускаем сбор пакета: проверяем только ленту
        db.get(Case, cid).status = CaseStatus.in_review
        db.commit()
    client.post(f"/api/staff/cases/{cid}/decision", json={"decision": "reject", "comment": "Похоже на транзит"})

    for_client = client.get(f"/api/cases/{cid}/messages").json()
    for_staff = client.get(f"/api/staff/cases/{cid}/messages").json()
    assert [m["from"] for m in for_client if m["text"] in ("Хорошо, ждём",)] == ["staff"]
    assert not any("транзит" in (m["text"] or "") for m in for_client)
    assert any("транзит" in (m["text"] or "") for m in for_staff)


def test_staff_downloads_client_document(client, samples):
    cid = _create(client, samples["honest_inn"])
    _upload_stub(client, cid, "npd_certificate")
    doc = client.get(f"/api/staff/cases/{cid}/report").json()["documents"][0]
    r = client.get(f"/api/staff/cases/{cid}/documents/{doc['id']}")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert client.get(f"/api/staff/cases/{cid}/documents/999999").status_code == 404


def test_stale_sqlite_schema_is_recreated(tmp_path, monkeypatch):
    """Старая база без новых колонок не роняет приложение: при смене схемы демо-база пересоздаётся."""
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, amount FLOAT)")
    from sqlalchemy import create_engine, inspect

    from app.db import session
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(session, "engine", engine)
    session.init_db()
    cols = {c["name"] for c in inspect(engine).get_columns("transactions")}
    assert {"auto_category", "client_comment"} <= cols


def test_demo_fills_required_documents(client, samples):
    """Кнопка «Подставить демо-документы»: всё обязательное загружено, чеки разобраны, PDF настоящий."""
    _load_bank(client, samples["honest_inn"], "honest_statement.csv")
    cid = _create(client, samples["honest_inn"])
    r = client.post(f"/api/demo/cases/{cid}/fill-documents").json()
    assert r["receipts"] > 50 and len(r["uploaded"]) == 3
    docs = [i for i in client.get(f"/api/cases/{cid}/checklist").json()["checklist"] if i["code"].startswith("doc:")]
    assert all(i["done"] for i in docs if i["required"])
    assert client.post(f"/api/demo/cases/{cid}/fill-documents").json()["uploaded"] == []  # повторно — ничего


def test_demo_synthetic_receipts_for_any_client(client, samples, tmp_path):
    """Для ИНН не из демо чеки генерируются по операциям из банка и проходят разбор как настоящие."""
    from tests.test_flow import SAMPLES
    inn = "500100732259"
    with (SAMPLES / "honest_statement.csv").open("rb") as f:
        client.put(f"/api/bank-core/{inn}/statement", files={"file": ("s.csv", f)})
    cid = _create(client, inn)
    r = client.post(f"/api/demo/cases/{cid}/fill-documents").json()
    assert r["receipts"] > 50
    report = client.get(f"/api/staff/cases/{cid}/report").json()
    assert report["matching"]["matched"] > 50 and report["matching"]["unmatched_income"]
