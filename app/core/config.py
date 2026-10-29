"""Настройки приложения.

Любое поле переопределяется переменной окружения или в .env, например NPD_CHECK_ENABLED=true.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Пути, внешние сервисы и пороги сверки и индикаторов риска."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./unblock.db"
    upload_dir: str = "uploads"

    # Эмуляция учётной системы банка: операции клиента лежат в bank_core_dir/<ИНН>.csv или .xlsx
    bank_core_dir: str = "samples/bank_core"
    bank_history_days: int = 180             # за сколько дней до блокировки банк берёт операции

    # Проверка статуса НПД через открытый API ФНС (statusnpd.nalog.ru).
    # Выключено по умолчанию, чтобы демо работало без интернета.
    npd_check_enabled: bool = False
    npd_check_url: str = "https://statusnpd.nalog.ru/api/v1/tracker/taxpayer_status"

    # Сопоставление поступлений и чеков
    match_amount_tolerance: float = 1.0      # руб., на копеечные расхождения и округления
    match_days_before: int = 3               # чек может быть выбит до поступления (предоплата)
    receipt_deadline_day: int = 9            # чек при неэлектронном расчёте — до 9 числа след. месяца (422-ФЗ, ст. 14)

    # Пороги индикаторов риска
    npd_annual_limit: float = 2_400_000
    coverage_warn: float = 0.8
    coverage_high: float = 0.5
    transit_warn: float = 0.7
    transit_high: float = 0.9
    cash_out_warn: float = 0.5
    night_share_warn: float = 0.3
    cancelled_share_warn: float = 0.1
    payers_per_month_warn: int = 40
    personal_share_warn: float = 0.2     # доля поступлений, которые клиент отнёс к личным
    personal_share_high: float = 0.5


settings = Settings()
