import os
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(path: str = ".env", *_args, **_kwargs):
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'").strip('"')
                    if key and key not in os.environ:
                        os.environ[key] = val
            return True
        except Exception:
            return False

load_dotenv()

OANDA_API_KEY    = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_ENV        = os.getenv("OANDA_ENVIRONMENT", os.getenv("OANDA_ENV", "practice"))

_DEFAULT_PAIRS = [
    "XAU_USD",
    "EUR_USD",
    "XAG_USD",
    "GBP_USD",
    "NZD_USD",
    "AUD_USD",
    "USD_CHF",
    "USD_JPY",
    "USD_CAD",
]
_pairs_raw = os.getenv("TRADING_PAIRS", "")
if _pairs_raw.strip():
    PAIRS = [p.strip().upper().replace("/", "_") for p in _pairs_raw.split(",") if p.strip()]
else:
    PAIRS = _DEFAULT_PAIRS

BASE_TIMEFRAME = "M1"
BASE_TIMEFRAME_SECONDS = 60

MTF_TIMEFRAMES = {
    "macro":   "H4",
    "confirm": "H1",
}

RISK_PERCENT    = 0.01
RR_RATIO        = 2.0
MIN_RR_RATIO    = 2.0
PREFERRED_RR_RATIO = 3.0

VOLUME_LOOKBACK      = 20
VOLUME_MULTIPLIER    = 1.2

SWING_LOOKBACK = 3

MTF_REFRESH_MINUTES = 15
MTF_CANDLE_COUNT    = 300

NEWS_BUFFER_MINUTES = 15
NEWS_HARD_BLOCK_HOURS = 8
NEWS_CURRENCIES     = {
    "EUR_USD": ["EUR", "USD"],
    "GBP_USD": ["GBP", "USD"],
    "XAU_USD": ["USD", "XAU"],
}

MAX_CONSECUTIVE_LOSSES_PER_DAY = 3
FRIDAY_CUTOFF_UTC_HOUR = 14
DECEMBER_BLACKOUT_START_DAY = 16
JANUARY_BLACKOUT_END_DAY = 15
