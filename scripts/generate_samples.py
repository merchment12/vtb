"""Синтетические пакеты для демо: честный репетитор и дроппер.

    uv run python scripts/generate_samples.py
"""

import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "samples"
START = datetime(2025, 6, 1)
END = datetime(2025, 9, 30)
PAGE = 100  # максимум чеков на странице GET /incomes


def make_inn(rng: random.Random) -> str:
    """Случайный ИНН физлица с правильными контрольными цифрами."""
    d = [rng.randint(0, 9) for _ in range(10)]
    k11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    d.append(sum(a * b for a, b in zip(k11, d)) % 11 % 10)
    k12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    d.append(sum(a * b for a, b in zip(k12, d)) % 11 % 10)
    return "".join(map(str, d))


def lknpd_income(uuid: str, name: str, operation: datetime, request: datetime, amount: float, inn: str, *,
                 client: str | None = None, client_inn: str | None = None, income_type: str = "FROM_INDIVIDUAL",
                 cancel: tuple[datetime, str] | None = None) -> dict:
    """Чек в формате ответа lknpd.nalog.ru (см. app/models/lknpd.py)."""
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S+03:00")  # noqa: E731
    return {
        "approvedReceiptUuid": uuid,
        "name": name,
        "services": [{"name": name, "quantity": 1, "serviceNumber": 0, "amount": amount}],
        "operationTime": iso(operation),
        "requestTime": iso(request),
        "registerTime": iso(request + timedelta(seconds=3)),
        "taxPeriodId": int(operation.strftime("%Y%m")),
        "paymentType": "CASH",
        "incomeType": income_type,
        "partnerCode": None,
        "totalAmount": amount,
        "cancellationInfo": cancel and {
            "operationTime": iso(cancel[0]), "registerTime": iso(cancel[0]),
            "taxPeriodId": int(cancel[0].strftime("%Y%m")), "comment": cancel[1],
        },
        "sourceDeviceId": None,
        "clientInn": client_inn,
        "clientDisplayName": client,
        "partnerDisplayName": None,
        "partnerInn": None,
        "inn": inn,
        "profession": "Репетитор",
        "description": [],
        "invoiceId": None,
    }


def write_statement(path: Path, rows: list[tuple[datetime, float, str, str]]) -> None:
    """Пишет выписку в CSV: «Дата операции; Сумма; Контрагент; Назначение платежа»."""
    rows.sort(key=lambda r: r[0])
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Дата операции", "Сумма", "Контрагент", "Назначение платежа"])
        for when, amount, cp, purpose in rows:
            w.writerow([when.strftime("%d.%m.%Y %H:%M"), f"{amount:.2f}".replace(".", ","), cp, purpose])


def days(rng: random.Random):
    """Перебирает дни демо-периода."""
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


def honest(rng: random.Random) -> str:
    """Честный репетитор: регулярные занятия почти все с чеками, аренда, налог. Возвращает ИНН."""
    inn = make_inn(rng)
    students = [f"{n} {s}." for n, s in zip(
        ["Ольга", "Дмитрий", "Ирина", "Сергей", "Мария", "Алексей", "Наталья", "Павел", "Елена", "Игорь", "Юлия", "Артём"],
        "КМСПВЛНОДТРЖ")]
    tx, receipts = [], []
    for d in days(rng):
        if d.weekday() < 6 and rng.random() < 0.55:
            for _ in range(rng.choice([1, 1, 2])):
                st = rng.choice(students)
                price = rng.choice([1800, 2000, 2500, 3000])
                when = d.replace(hour=rng.randint(10, 20), minute=rng.randint(0, 59))
                tx.append((when, price, st, "Перевод по СБП"))
                r = rng.random()
                if r < 0.85:
                    created = when + timedelta(minutes=rng.randint(1, 30))
                    receipts.append((created, created, price, st))
                elif r < 0.95:
                    # выбит через несколько дней, но дата расчёта проставлена задним числом
                    receipts.append((when, when + timedelta(days=rng.randint(1, 5)), price, st))
        if d.day == 5:
            tx.append((d.replace(hour=9), -25000, "ИП Смирнова", "Оплата аренды кабинета"))
        if d.day == 25:
            tx.append((d.replace(hour=12), -2400, "УФК по г. Москве", "Налог на профессиональный доход"))
        if rng.random() < 0.4:
            tx.append((d.replace(hour=rng.randint(9, 21)), -round(rng.uniform(300, 4000), 2), "Пятёрочка", "Оплата покупки"))
    tx.append((datetime(2025, 7, 14, 18, 3), 15000, "Белова Т. И.", "Перевод по СБП"))  # от мамы — без чека
    tx.append((datetime(2025, 8, 2, 11, 40), 5000, "Перевод между своими счетами", "Перевод между своими счетами"))
    tx.append((datetime(2025, 8, 20, 13, 0), -10000, "Банкомат ВТБ", "Снятие наличных"))
    write_statement(OUT / "honest_statement.csv", tx)

    content = [
        lknpd_income(f"20{i:08d}", "Занятие по английскому языку", op, req, amount, inn, client=cp)
        for i, (op, req, amount, cp) in enumerate(receipts)
    ]
    # один общий чек на два занятия, аннулированный чек и оплата от компании (ставка 6%)
    content.append(lknpd_income("2099999997", "Пакет 2 занятий", datetime(2025, 9, 29, 21), datetime(2025, 9, 29, 21),
                                4000, inn, client="Ольга К."))
    content.append(lknpd_income("2099999998", "Занятие", datetime(2025, 7, 1, 10), datetime(2025, 7, 1, 10), 2000, inn,
                                client="Павел Д.", cancel=(datetime(2025, 7, 3, 12), "Возврат средств")))
    content.append(lknpd_income("2099999999", "Корпоративный курс английского", datetime(2025, 8, 12, 14, 5),
                                datetime(2025, 8, 12, 14, 5), 30000, inn, client="ООО «Лингва Плюс»",
                                client_inn="7704211201", income_type="FROM_LEGAL_ENTITY"))
    tx_extra = [(datetime(2025, 9, 29, 15, 0), 2000, "Ольга К.", "Перевод по СБП"),
                (datetime(2025, 9, 29, 19, 0), 2000, "Ольга К.", "Перевод по СБП"),
                (datetime(2025, 8, 12, 14, 0), 30000, "ООО Лингва Плюс", "Оплата по договору 12 за курс английского")]
    with (OUT / "honest_statement.csv").open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        for when, amount, cp, purpose in tx_extra:
            w.writerow([when.strftime("%d.%m.%Y %H:%M"), f"{amount:.2f}".replace(".", ","), cp, purpose])
    # так отдаёт GET /api/v1/incomes: страницы до 100 чеков
    pages = [
        {"content": content[off:off + PAGE], "hasMore": off + PAGE < len(content), "currentOffset": off,
         "currentLimit": PAGE}
        for off in range(0, len(content), PAGE)
    ]
    (OUT / "honest_receipts.json").write_text(json.dumps(pages, ensure_ascii=False, indent=1), "utf-8")
    return inn


def dropper(rng: random.Random) -> str:
    """Дроппер: ночные переводы от сотен людей, деньги уходят за пару часов, чеков мало. Возвращает ИНН."""
    inn = make_inn(rng)
    tx, receipts = [], []
    for d in days(rng):
        for _ in range(rng.randint(2, 6)):
            when = d.replace(hour=rng.choice([0, 1, 2, 3, 13, 22, 23]), minute=rng.randint(0, 59))
            amount = rng.choice([5000, 10000, 15000, 20000, 30000, 50000])
            payer = f"Плательщик {rng.randint(1, 900)}"
            tx.append((when, amount, payer, "Перевод по СБП"))
            if rng.random() < 0.15:
                receipts.append((when, amount))
            out = when + timedelta(minutes=rng.randint(5, 180))
            if rng.random() < 0.6:
                tx.append((out, -amount * 0.98, f"Получатель {rng.randint(1, 50)}", "Перевод по номеру телефона"))
            else:
                tx.append((out, -amount, "Банкомат", "Снятие наличных"))
    write_statement(OUT / "dropper_statement.csv", tx)
    with (OUT / "dropper_receipts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Дата", "Сумма", "Наименование", "Статус"])
        for when, amount in receipts:
            status = "Аннулирован" if rng.random() < 0.3 else "Действителен"
            w.writerow([when.strftime("%d.%m.%Y %H:%M"), amount, "Консультация", status])
    return inn


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    rng = random.Random(42)
    info = {"honest_inn": honest(rng), "dropper_inn": dropper(rng), "blocked_at": "2025-10-01"}
    # операции по карте — в эмулятор учётной системы банка, клиент их не загружает
    (OUT / "bank_core").mkdir(exist_ok=True)
    for name in ("honest", "dropper"):
        (OUT / "bank_core" / f"{info[name + '_inn']}.csv").write_bytes((OUT / f"{name}_statement.csv").read_bytes())
    (OUT / "README.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(info, ensure_ascii=False))
