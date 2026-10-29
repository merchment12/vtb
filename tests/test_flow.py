"""Сквозной сценарий на синтетике: честный репетитор разблокирован за один круг, дроппер подсвечен."""

from tests.conftest import ROOT

SAMPLES = ROOT / "samples"


def _load_bank(client, inn, name):
    """Кладёт операции клиента в эмулятор банка, как это сделала бы АБС."""
    with (SAMPLES / name).open("rb") as f:
        r = client.put(f"/api/bank-core/{inn}/statement", files={"file": (name, f)})
    assert r.status_code == 200, r.text
    return r.json()["transactions"]


def _create(client, inn, reason="income_source"):
    r = client.post("/api/cases", json={
        "inn": inn, "full_name": "Тест Тестович", "block_reason": reason, "blocked_at": "2025-10-01",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upload(client, case_id, doc_type, name):
    with (SAMPLES / name).open("rb") as f:
        r = client.post(f"/api/cases/{case_id}/documents", params={"doc_type": doc_type}, files={"file": (name, f)})
    assert r.status_code == 200, r.text
    return r.json()


def _upload_stub(client, case_id, doc_type):
    r = client.post(f"/api/cases/{case_id}/documents", params={"doc_type": doc_type},
                    files={"file": (f"{doc_type}.pdf", b"%PDF-1.4 stub")})
    assert r.status_code == 200, r.text


def test_honest_flow(client, samples):
    assert _load_bank(client, samples["honest_inn"], "honest_statement.csv") > 100
    cid = _create(client, samples["honest_inn"])
    assert len(client.get(f"/api/cases/{cid}/transactions").json()) > 100  # подтянулись из банка сами
    _upload(client, cid, "receipts", "honest_receipts.json")
    _upload_stub(client, cid, "npd_certificate")
    _upload_stub(client, cid, "income_certificate")

    # без пояснений к поступлениям без чека отправить нельзя
    r = client.post(f"/api/cases/{cid}/submit")
    assert r.status_code == 409
    assert "Каждое поступление без чека пояснено" in r.json()["detail"]["missing"]

    for tx in client.get(f"/api/cases/{cid}/transactions", params={"only_unmatched": True}).json():
        if tx["amount"] == 15000:
            body = {"category": "personal", "client_comment": "Перевод от мамы на день рождения"}
        else:
            body = {"client_comment": "Оплата занятия, чек выбит наличными позже — пропустила"}
        assert client.patch(f"/api/cases/{cid}/transactions/{tx['id']}", json=body).status_code == 200

    # повторная загрузка операций из банка не стирает пояснения клиента
    client.post(f"/api/staff/cases/{cid}/transactions/sync")
    assert client.get(f"/api/cases/{cid}/checklist").json()["ready_to_submit"]
    assert client.post(f"/api/cases/{cid}/submit").json()["status"] == "submitted"

    report = client.get(f"/api/staff/cases/{cid}/report").json()
    levels = {i["code"]: i["level"] for i in report["indicators"]}
    assert levels["receipt_coverage"] == "ok"
    assert levels["transit"] == "ok"
    assert "seller_inn_mismatch" not in levels
    assert report["verdict"] != "high_risk"
    assert any(m["kind"] == "many_tx" for m in report["matching"]["matches"])

    r = client.post(f"/api/staff/cases/{cid}/decision", json={"decision": "unblock", "comment": "Доход подтверждён"})
    assert r.json()["status"] == "unblocked"
    assert r.json()["request_rounds"] == 0


def test_dropper_flagged(client, samples):
    _load_bank(client, samples["dropper_inn"], "dropper_statement.csv")
    cid = _create(client, samples["dropper_inn"], reason="transit")
    _upload(client, cid, "receipts", "dropper_receipts.csv")
    _upload_stub(client, cid, "npd_certificate")
    _upload_stub(client, cid, "explanation")
    for tx in client.get(f"/api/cases/{cid}/transactions", params={"only_unmatched": True}).json():
        client.patch(f"/api/cases/{cid}/transactions/{tx['id']}", json={"client_comment": "оплата услуг"})
    assert client.post(f"/api/cases/{cid}/submit").status_code == 200

    report = client.get(f"/api/staff/cases/{cid}/report").json()
    levels = {i["code"]: i["level"] for i in report["indicators"]}
    assert report["verdict"] == "high_risk"
    assert levels["receipt_coverage"] == "high"
    assert levels["transit"] == "high"
    assert levels["npd_limit"] == "high"
    assert report["request_draft"]

    r = client.post(f"/api/staff/cases/{cid}/request-docs", json={})
    assert r.json()["status"] == "docs_requested" and r.json()["request_rounds"] == 1


def test_client_cannot_upload_statement(client, samples):
    """Выписку клиент не загружает: подделать движение денег по карте он не может."""
    cid = _create(client, samples["honest_inn"])
    r = client.post(f"/api/cases/{cid}/documents", params={"doc_type": "statement"},
                    files={"file": ("s.csv", "Дата;Сумма\n01.06.2025;100\n".encode())})
    assert r.status_code == 422


def test_case_without_bank_data_cannot_be_submitted(client):
    """Нет операций из банка — пакет не уходит, а в черновике запроса клиенту этого пункта нет."""
    r = client.post("/api/cases", json={"inn": "500100732259", "full_name": "Т", "block_reason": "income_source"})
    checklist = client.get(f"/api/cases/{r.json()['id']}/checklist").json()["checklist"]
    item = next(i for i in checklist if i["code"] == "bank_transactions")
    assert item["required"] and not item["done"]


def test_analytics_and_finmodel(client):
    s = client.get("/api/analytics/summary").json()
    assert s["decided"] >= 1
    fm = client.post("/api/finmodel", json={}).json()["result"]
    assert fm["effect_per_year"] > 0
    sc = client.get("/api/finmodel/scenarios").json()
    assert sc["pessimistic"]["effect_per_year"] < sc["base"]["effect_per_year"] < sc["optimistic"]["effect_per_year"]


def test_hiding_income_as_personal_is_flagged(client, samples):
    """Клиент перевёл почти весь доход в «личные», чтобы не показывать чеки: покрытие 100%, но сотрудник это видит."""
    _load_bank(client, samples["honest_inn"], "honest_statement.csv")
    cid = _create(client, samples["honest_inn"])
    _upload(client, cid, "receipts", "honest_receipts.json")
    _upload_stub(client, cid, "npd_certificate")
    _upload_stub(client, cid, "income_certificate")
    income = [t for t in client.get(f"/api/cases/{cid}/transactions").json() if t["category"] == "business_income"]
    for t in income[3:]:
        client.patch(f"/api/cases/{cid}/transactions/{t['id']}", json={"category": "personal", "client_comment": "подарок"})
    for t in client.get(f"/api/cases/{cid}/transactions", params={"only_unmatched": True}).json():
        client.patch(f"/api/cases/{cid}/transactions/{t['id']}", json={"category": "personal", "client_comment": "подарок"})

    checklist = client.get(f"/api/cases/{cid}/checklist").json()
    assert checklist["ready_to_submit"]
    assert "receipt_coverage" not in checklist  # значения индикаторов риска клиенту не отдаём
    assert checklist["left_to_explain"] == 0
    assert client.post(f"/api/cases/{cid}/submit").status_code == 200

    report = client.get(f"/api/staff/cases/{cid}/report").json()
    levels = {i["code"]: i["level"] for i in report["indicators"]}
    assert levels["personal_relabel"] == "high"
    assert report["verdict"] == "high_risk"
    assert all(x["relabeled"] for x in report["matching"]["personal_income"])


def test_honest_personal_transfer_is_not_flagged(client, samples):
    """Один перевод от мамы — нормальная ситуация, индикатор не срабатывает."""
    _load_bank(client, samples["honest_inn"], "honest_statement.csv")
    cid = _create(client, samples["honest_inn"])
    _upload(client, cid, "receipts", "honest_receipts.json")
    tx = next(t for t in client.get(f"/api/cases/{cid}/transactions", params={"only_unmatched": True}).json()
              if t["amount"] == 15000)
    client.patch(f"/api/cases/{cid}/transactions/{tx['id']}", json={"category": "personal", "client_comment": "от мамы"})
    report = client.get(f"/api/staff/cases/{cid}/report").json()
    assert {i["code"]: i["level"] for i in report["indicators"]}["personal_relabel"] == "ok"


def test_demo_seed_and_staff_page(client):
    """Демо-очередь из трёх кейсов и экран сотрудника для защиты."""
    ids = client.post("/api/demo/seed").json()["case_ids"]
    verdicts = [client.get(f"/api/staff/cases/{i}/report").json()["verdict"] for i in ids]
    assert verdicts[0] != "high_risk" and verdicts[1] == "high_risk" and verdicts[2] == "high_risk"
    report = client.get(f"/api/staff/cases/{ids[0]}/report").json()
    assert report["matching"]["pairs"] and "build_ms" in report
    page = client.get("/staff")
    assert page.status_code == 200 and "Разбор блокировок" in page.text


def test_finmodel_matches_spreadsheet():
    """Базовый сценарий совпадает с таблицей финмодели (лист «Расчет»)."""
    from app.services.analytics.finmodel import FinInput, calculate

    r = calculate(FinInput())
    assert r["cases_per_year"] == 20_000
    assert r["review_saving"] == 33_720_000
    assert r["repeat_request_saving"] == 5_058_000
    assert r["retention_revenue"] == 3_600_000
    assert r["effect_per_year"] == 42_378_000
    assert r["net_per_year"] == 40_698_000
