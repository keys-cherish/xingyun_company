"""Configuration management using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Telegram
    bot_token: str = ""
    proxy_url: str = ""  # HTTP代理，如 http://127.0.0.1:7890
    # Comma-separated list of allowed chat_ids (group/subchannel) where commands work.
    # Empty means all groups are allowed.
    allowed_chat_ids: str = ""
    # 允许的论坛话题ID（message_thread_id）。0 表示不限制话题。
    allowed_topic_thread_id: int = 0

    # Database
    database_url: str = "postgresql+asyncpg://mycompany:mycompany@localhost:5432/mycompany"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Game constants
    initial_traffic: int = 100000
    company_creation_cost: int = 55000
    min_owner_share_pct: int = 30  # owner must hold >= 30%
    valuation_fund_coeff: float = 1.0
    valuation_income_days: int = 30
    daily_operating_cost_pct: float = 0.07
    dividend_pct: float = 0.70

    # Research
    base_research_cost: int = 2500
    base_research_seconds: int = 3600  # 1 hour default

    # Product
    product_create_cost: int = 1500
    product_upgrade_cost_base: int = 800
    product_upgrade_income_pct: float = 0.20  # +20% per upgrade

    # Roadshow
    roadshow_cost: int = 700
    roadshow_cooldown_seconds: int = 7200  # 2 hours

    # Reputation buff
    max_reputation_buff_pct: float = 0.50  # max 50% revenue buff
    reputation_per_research: int = 5
    reputation_per_cooperation: int = 10
    reputation_per_dividend: int = 3

    # Settlement
    settlement_hour: int = 0  # midnight UTC
    settlement_minute: int = 0

    # Tax system
    tax_rate: float = 0.06
    social_insurance_rate: float = 0.02

    # Employee system
    base_employee_limit: int = 5  # starting max employees
    employee_limit_per_level: int = 3  # +3 slots per company level
    employee_salary_base: int = 80  # base daily salary per employee

    # Random events
    event_chance: float = 0.35  # 35% chance per company per day

    # AI API (for future AI dialogue features)
    ai_api_key: str = ""
    ai_api_base_url: str = ""
    ai_model: str = ""

    # 流量来源接口（预置参数，后续接入外部API）
    traffic_api_url: str = ""  # 外部流量接口URL
    traffic_api_key: str = ""  # 外部接口认证密钥
    traffic_total_pool: int = 10_000_000  # 全局流量池总量
    traffic_daily_distribution: int = 100_000  # 每日可分配流量
    traffic_exchange_rate: float = 1.0  # 外部积分兑换流量汇率

    # 管理员
    super_admin_tg_id: int = 0  # 兼容旧配置：单个超级管理员TG ID（高危命令）
    super_admin_tg_ids: str = ""  # 新配置：逗号分隔的超级管理员TG ID列表
    admin_tg_ids: str = ""  # 逗号分隔的管理员TG ID列表
    admin_secret_key: str = ""  # 管理员认证密钥

    @property
    def admin_tg_id_set(self) -> set[int]:
        if not self.admin_tg_ids.strip():
            return set()
        return {int(x.strip()) for x in self.admin_tg_ids.split(",") if x.strip()}

    @property
    def super_admin_tg_id_set(self) -> set[int]:
        ids: set[int] = set()
        if self.super_admin_tg_ids.strip():
            ids.update({int(x.strip()) for x in self.super_admin_tg_ids.split(",") if x.strip()})
        if self.super_admin_tg_id > 0:
            ids.add(self.super_admin_tg_id)
        return ids

    @property
    def allowed_chat_id_set(self) -> set[int]:
        if not self.allowed_chat_ids.strip():
            return set()
        return {int(x.strip()) for x in self.allowed_chat_ids.split(",") if x.strip()}


settings = Settings()
