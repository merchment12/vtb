"""Финмодель в коде: те же допущения и формулы, что в таблице «финмодель.xlsx», чтобы на демо крутить сценарии.

Если меняете число здесь — поменяйте и в таблице (и наоборот): на защите цифры должны совпадать.
"""

from pydantic import BaseModel, Field


class FinInput(BaseModel):
    """Допущения финмодели. В description каждого поля — откуда взято значение."""

    vtb_self_employed: int = Field(1_000_000, description="Самозанятые клиенты ВТБ — факт: «более 1 млн» (tmgnews.ru), нижняя граница")
    block_rate: float = Field(0.02, description="Доля заблокированных за год — средняя по рынку, ~2% (Коммерсантъ)")
    cases_per_person: float = Field(1.0, description="Блокировок на человека в год — консервативно 1")
    honest_share: float = Field(0.5, description="Доля честных среди заблокированных — оценка команды")

    review_hours_now: float = Field(3.0, description="Часов сотрудника на первичную проверку кейса сейчас — оценка команды")
    review_hours_tool: float = Field(1.0, description="Часов с инструментом — проверяется на пилоте")
    repeat_share_now: float = Field(0.5, description="Доля кейсов с повторным запросом документов сейчас — допущение (ВТБ, ТАСС: задержки из-за неполных документов)")
    repeat_share_tool: float = Field(0.2, description="Доля кейсов с повторным запросом с инструментом — система не пускает неполный пакет")
    hours_per_repeat: float = Field(1.0, description="Часов сотрудника на один повторный круг — допущение")
    hour_cost: float = Field(843, description="Стоимость часа сотрудника, ₽ — средняя зарплата")

    churn_after_block: float = Field(0.3, description="Доля честных, уходящих после блокировки — интервью и отзывы banki.ru")
    retained_share: float = Field(0.15, description="Доля ушедших, которых удержим быстрым разбором — уточняется на пилоте")
    revenue_per_client: float = Field(8000, description="Доход банка с клиента в год, ₽ — середина диапазона 4 000–12 000")

    capex: float = Field(10_697_200, description="Разовые затраты на внедрение, ₽ — расчёт в таблице (лист «Расчет»)")
    opex_year: float = Field(1_680_000, description="Ежегодные затраты, ₽ — поддержка 20% от разработки + серверы")
    cost_multiplier: float = Field(1.0, description="Множитель затрат для сценариев")


def calculate(p: FinInput) -> dict:
    """Кейсы в год, экономия на первичной проверке и повторных запросах, удержание, эффект и окупаемость."""
    people = p.vtb_self_employed * p.block_rate
    cases = people * p.cases_per_person
    hours_review = cases * (p.review_hours_now - p.review_hours_tool)
    hours_repeat = cases * (p.repeat_share_now - p.repeat_share_tool) * p.hours_per_repeat

    review_saving = hours_review * p.hour_cost
    repeat_saving = hours_repeat * p.hour_cost
    retention = people * p.honest_share * p.churn_after_block * p.retained_share * p.revenue_per_client

    capex = p.capex * p.cost_multiplier
    opex = p.opex_year * p.cost_multiplier
    effect = review_saving + repeat_saving + retention
    net = effect - opex
    return {
        "cases_per_year": round(cases),
        "review_saving": round(review_saving),
        "repeat_request_saving": round(repeat_saving),
        "retention_revenue": round(retention),
        "effect_per_year": round(effect),
        "capex": round(capex),
        "opex_year": round(opex),
        "net_per_year": round(net),
        "payback_months": round(capex / (net / 12), 1) if net > 0 else None,
        "cumulative_3y": round(3 * net - capex),
        "staff_hours_saved": round(hours_review + hours_repeat),
    }


# те же значения, что на листе «Сценарии» в таблице
PESSIMISTIC = {"block_rate": 0.01, "honest_share": 0.3, "review_hours_now": 2.0, "review_hours_tool": 1.5,
               "repeat_share_now": 0.4, "repeat_share_tool": 0.3, "churn_after_block": 0.2,
               "retained_share": 0.1, "revenue_per_client": 4000, "cost_multiplier": 1.5}
OPTIMISTIC = {"block_rate": 0.03, "honest_share": 0.7, "review_hours_now": 4.0, "review_hours_tool": 0.5,
              "repeat_share_now": 0.6, "repeat_share_tool": 0.1, "churn_after_block": 0.4,
              "retained_share": 0.25, "revenue_per_client": 12000, "cost_multiplier": 0.8}


def scenarios() -> dict[str, dict]:
    """Пессимистичный / базовый / оптимистичный по самым спорным допущениям."""
    base = FinInput()
    return {
        "pessimistic": calculate(base.model_copy(update=PESSIMISTIC)),
        "base": calculate(base),
        "optimistic": calculate(base.model_copy(update=OPTIMISTIC)),
    }
