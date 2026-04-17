from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv


DEFAULT_PAIRS = [
    "EUR_USD",
    "GBP_USD",
    "USD_JPY",
    "AUD_USD",
    "USD_CHF",
    "USD_CAD",
    "NZD_USD",
    "XAU_USD",
    "XAG_USD",
]


def _parse_pairs(raw: str) -> List[str]:
    if not raw:
        return list(DEFAULT_PAIRS)
    pairs = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        p = part.upper().replace("/", "_")
        if "_" not in p and len(p) == 6:
            p = f"{p[:3]}_{p[3:]}"
        pairs.append(p)
    return pairs or list(DEFAULT_PAIRS)


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def build_postgres_url() -> str:
    direct = os.getenv("DATABASE_URL", "").strip()
    if direct:
        return direct

    user = os.getenv("POSTGRES_USER", "").strip()
    pwd = os.getenv("POSTGRES_PASSWORD", "").strip()
    host = os.getenv("POSTGRES_HOST", "").strip()
    port = os.getenv("POSTGRES_PORT", "5432").strip()
    db = os.getenv("POSTGRES_DB", "").strip()
    if user and pwd and host and db:
        return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"
    return ""


@dataclass
class Settings:
    oanda_api_key: str = ""
    oanda_account_id: str = ""
    oanda_environment: str = "practice"
    pairs: List[str] = field(default_factory=lambda: list(DEFAULT_PAIRS))
    chart_pairs: List[str] = field(default_factory=lambda: list(DEFAULT_PAIRS))
    base_timeframe: str = "M1"
    mtf_timeframes: Dict[str, str] = field(default_factory=lambda: {"macro": "H4", "confirm": "H1"})
    risk_per_trade: float = 0.01
    min_rr: float = 2.0
    max_open_positions: int = 5
    daily_loss_limit_pct: float = 3.0
    stop_after_consecutive_losses: int = 3
    news_block_hours: int = 4
    use_social_sentiment: bool = True
    use_intermarket: bool = True
    use_paper_mode: bool = True
    paper_start_balance: float = 1000.0
    mlflow_tracking_uri: str = ""
    database_url: str = ""
    db_type: str = "json"
    data_dir: str = "output"
    cache_dir: str = "output/cache"
    signals_path: str = "output/signals.jsonl"
    journal_path: str = "output/trading_journal.csv"
    metrics_path: str = "output/metrics.json"
    news_api_key: str = ""
    tradingeconomics_api_key: str = ""
    twitter_bearer_token: str = ""
    reddit_feed_url: str = "https://www.reddit.com/r/stocks/hot.json"
    stocktwits_rss_url: str = "https://stocktwits.com/symbol/AAPL.rss"
    streamlit_port: int = 8501
    dash_port: int = 8050
    api_host: str = "127.0.0.1"
    chart_refresh_ms: int = 2000
    debug: bool = False

    def __getattr__(self, name: str):
        # Recovery mode compatibility: legacy compiled modules may request
        # optional settings that are not present in this source snapshot.
        return None


def load_settings() -> Settings:
    env_path = Path.cwd() / ".env"
    try:
        load_dotenv(dotenv_path=env_path if env_path.exists() else None, override=False)
    except Exception:
        # Keep startup resilient in restricted execution environments.
        pass

    environment = os.getenv("OANDA_ENVIRONMENT", os.getenv("OANDA_ENV", "practice")).strip().lower() or "practice"

    settings = Settings(
        oanda_api_key=os.getenv("OANDA_API_KEY", "").strip(),
        oanda_account_id=os.getenv("OANDA_ACCOUNT_ID", "").strip(),
        oanda_environment=environment,
        pairs=_parse_pairs(os.getenv("TRADING_PAIRS", "")),
        chart_pairs=_parse_pairs(os.getenv("CHART_PAIRS", os.getenv("TRADING_PAIRS", ""))),
        base_timeframe=os.getenv("BASE_TIMEFRAME", "M1").strip().upper() or "M1",
        risk_per_trade=_env_float("RISK_PER_TRADE", 0.01),
        min_rr=_env_float("MIN_RR", 2.0),
        max_open_positions=_env_int("MAX_OPEN_POSITIONS", 5),
        daily_loss_limit_pct=_env_float("DAILY_LOSS_LIMIT_PCT", 3.0),
        stop_after_consecutive_losses=_env_int("STOP_AFTER_CONSECUTIVE_LOSSES", 3),
        news_block_hours=_env_int("NEWS_BLOCK_HOURS", 4),
        use_social_sentiment=_env_bool("USE_SOCIAL_SENTIMENT", True),
        use_intermarket=_env_bool("USE_INTERMARKET", True),
        use_paper_mode=_env_bool("USE_PAPER_MODE", True),
        paper_start_balance=_env_float("PAPER_START_BALANCE", 1000.0),
        mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI", "").strip(),
        database_url=build_postgres_url(),
        db_type=os.getenv("DB_TYPE", "json").strip().lower() or "json",
        data_dir=os.getenv("DATA_DIR", "output").strip() or "output",
        cache_dir=os.getenv("CACHE_DIR", "output/cache").strip() or "output/cache",
        signals_path=os.getenv("SIGNALS_PATH", "output/signals.jsonl").strip() or "output/signals.jsonl",
        journal_path=os.getenv("JOURNAL_PATH", "output/trading_journal.csv").strip() or "output/trading_journal.csv",
        metrics_path=os.getenv("METRICS_PATH", "output/metrics.json").strip() or "output/metrics.json",
        news_api_key=os.getenv("NEWS_API_KEY", "").strip(),
        tradingeconomics_api_key=os.getenv("TRADINGECONOMICS_API_KEY", "").strip(),
        twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN", "").strip(),
        reddit_feed_url=os.getenv("REDDIT_FEED_URL", "https://www.reddit.com/r/stocks/hot.json").strip(),
        stocktwits_rss_url=os.getenv("STOCKTWITS_RSS_URL", "https://stocktwits.com/symbol/AAPL.rss").strip(),
        streamlit_port=_env_int("STREAMLIT_PORT", 8501),
        dash_port=_env_int("DASH_PORT", 8050),
        api_host=os.getenv("API_HOST", "127.0.0.1").strip() or "127.0.0.1",
        chart_refresh_ms=_env_int("CHART_REFRESH_MS", 2000),
        debug=_env_bool("DEBUG", False),
    )
    return settings


SETTINGS = load_settings()
