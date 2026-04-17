import os
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request


load_dotenv()

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_ENV = os.getenv("OANDA_ENVIRONMENT", os.getenv("OANDA_ENV", "practice")).lower()

BASE_URL = "https://api-fxpractice.oanda.com" if OANDA_ENV != "live" else "https://api-fxtrade.oanda.com"

PAIRS = [
    "XAU_USD",
    "EUR_USD",
    "BTC_USD",
    "XAG_USD",
    "GBP_USD",
    "NZD_USD",
    "AUD_USD",
    "USD_CHF",
    "USD_JPY",
    "USD_CAD",
]

TIMEFRAMES = {"M15": 15 * 60, "H1": 60 * 60, "H4": 4 * 60 * 60}
YAHOO_SYMBOLS = {
    "EUR_USD": "EURUSD=X",
    "GBP_USD": "GBPUSD=X",
    "USD_JPY": "USDJPY=X",
    "AUD_USD": "AUDUSD=X",
    "USD_CHF": "USDCHF=X",
    "USD_CAD": "USDCAD=X",
    "NZD_USD": "NZDUSD=X",
    "XAU_USD": "XAUUSD=X",
    "XAG_USD": "XAGUSD=X",
    "BTC_USD": "BTC-USD",
}

app = Flask(__name__)
session = requests.Session()
if OANDA_API_KEY:
    session.headers.update({"Authorization": f"Bearer {OANDA_API_KEY}"})

_available_pairs_cache: List[str] = []
_available_pairs_cache_ts: Optional[datetime] = None

_candle_cache: Dict[Tuple[str, str, str], Dict] = {}
_candle_cache_lock = Lock()
_price_cache: Dict[str, Dict] = {}
_price_cache_lock = Lock()

CANDLE_CACHE_TTL_SECONDS = {"M15": 0.8, "H1": 1.0, "H4": 1.2}
PRICE_CACHE_TTL_SECONDS = 0.8


def normalize_pair(pair: str) -> str:
    pair = (pair or "").strip().upper().replace("/", "_")
    if "_" in pair:
        return pair
    if len(pair) == 6:
        return f"{pair[:3]}_{pair[3:]}"
    return pair


def parse_oanda_time(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def tf_seconds(timeframe: str) -> int:
    return TIMEFRAMES.get(timeframe, 15 * 60)


def get_candles(pair: str, timeframe: str, count: int = 500) -> List[Dict]:
    url = f"{BASE_URL}/v3/instruments/{pair}/candles"
    params = {"granularity": timeframe, "count": max(50, min(1500, int(count))), "price": "M"}
    resp = session.get(url, params=params, timeout=6)
    resp.raise_for_status()
    payload = resp.json()

    out = []
    for c in payload.get("candles", []):
        mid = c.get("mid", {})
        t = parse_oanda_time(c["time"])
        out.append(
            {
                "time": int(t.timestamp()),
                "open": float(mid.get("o", 0.0)),
                "high": float(mid.get("h", 0.0)),
                "low": float(mid.get("l", 0.0)),
                "close": float(mid.get("c", 0.0)),
                "volume": int(c.get("volume", 0)),
                "complete": bool(c.get("complete", False)),
            }
        )
    return out


def _aggregate_to_h4(rows: List[Dict]) -> List[Dict]:
    buckets: Dict[int, Dict] = {}
    order: List[int] = []
    for row in rows:
        ts = int(row["time"])
        bucket_start = ts - (ts % (4 * 60 * 60))
        if bucket_start not in buckets:
            buckets[bucket_start] = {
                "time": bucket_start,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row.get("volume", 0)),
            }
            order.append(bucket_start)
        else:
            b = buckets[bucket_start]
            b["high"] = max(float(b["high"]), float(row["high"]))
            b["low"] = min(float(b["low"]), float(row["low"]))
            b["close"] = float(row["close"])
            b["volume"] = int(b.get("volume", 0)) + int(row.get("volume", 0))
    return [buckets[k] for k in sorted(order)]


def get_candles_yahoo(pair: str, timeframe: str, count: int = 500) -> List[Dict]:
    symbol = YAHOO_SYMBOLS.get(pair)
    if not symbol:
        raise ValueError(f"No Yahoo mapping for {pair}")

    if timeframe == "M15":
        interval, lookback_range = "15m", "10d"
    elif timeframe == "H1":
        interval, lookback_range = "60m", "60d"
    else:  # H4
        interval, lookback_range = "60m", "730d"

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "interval": interval,
        "range": lookback_range,
        "includePrePost": "false",
        "events": "div,splits",
    }
    resp = requests.get(url, params=params, timeout=6)
    resp.raise_for_status()
    payload = resp.json()

    result = payload.get("chart", {}).get("result", [])
    if not result:
        raise ValueError("Yahoo returned empty chart result")
    node = result[0]
    timestamps = node.get("timestamp", []) or []
    quote = (node.get("indicators", {}).get("quote", [{}]) or [{}])[0]
    opens = quote.get("open", []) or []
    highs = quote.get("high", []) or []
    lows = quote.get("low", []) or []
    closes = quote.get("close", []) or []
    volumes = quote.get("volume", []) or []

    rows: List[Dict] = []
    for i, ts in enumerate(timestamps):
        if i >= len(opens) or i >= len(highs) or i >= len(lows) or i >= len(closes):
            continue
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        if o is None or h is None or l is None or c is None:
            continue
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
        rows.append(
            {
                "time": int(ts),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": int(v),
            }
        )

    if timeframe == "H4":
        rows = _aggregate_to_h4(rows)

    if not rows:
        raise ValueError("Yahoo returned no candle rows")

    now_epoch = int(datetime.now(timezone.utc).timestamp())
    tf_sec = tf_seconds(timeframe)
    for r in rows:
        r["complete"] = (int(r["time"]) + tf_sec) <= now_epoch

    return rows[-max(50, min(1500, int(count))):]


def get_last_price_from_rows(rows: List[Dict]) -> Optional[float]:
    if not rows:
        return None
    try:
        return float(rows[-1]["close"])
    except Exception:
        return None


def _copy_rows(rows: List[Dict]) -> List[Dict]:
    return [dict(r) for r in rows]


def _get_candle_cache_ttl(timeframe: str) -> float:
    return CANDLE_CACHE_TTL_SECONDS.get(timeframe, 3.0)


def get_candles_cached(
    source: str, pair: str, timeframe: str, count: int, fetch_fn
) -> Tuple[List[Dict], bool]:
    key = (source, pair, timeframe)
    ttl = _get_candle_cache_ttl(timeframe)
    now = datetime.now(timezone.utc)

    with _candle_cache_lock:
        cached = _candle_cache.get(key)
        if cached:
            age = (now - cached["ts"]).total_seconds()
            if age <= ttl:
                return _copy_rows(cached["rows"]), False

    try:
        rows = fetch_fn()
        with _candle_cache_lock:
            _candle_cache[key] = {"rows": rows, "ts": now}
        return _copy_rows(rows), False
    except Exception:
        with _candle_cache_lock:
            cached = _candle_cache.get(key)
            if cached:
                return _copy_rows(cached["rows"]), True
        raise


def get_available_pairs(force_refresh: bool = False) -> List[str]:
    global _available_pairs_cache, _available_pairs_cache_ts
    if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
        return PAIRS

    now = datetime.now(timezone.utc)
    if (
        not force_refresh
        and _available_pairs_cache
        and _available_pairs_cache_ts
        and (now - _available_pairs_cache_ts).total_seconds() < 300
    ):
        return _available_pairs_cache

    try:
        url = f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/instruments"
        resp = session.get(url, timeout=12)
        resp.raise_for_status()
        instruments = resp.json().get("instruments", [])
        inst_names = {i.get("name", "") for i in instruments}
        filtered = [p for p in PAIRS if p in inst_names]
        _available_pairs_cache = filtered or PAIRS
        _available_pairs_cache_ts = now
    except Exception:
        # Keep chart usable even when instrument listing fails.
        if not _available_pairs_cache:
            _available_pairs_cache = PAIRS
        _available_pairs_cache_ts = now

    return _available_pairs_cache


def get_price(pair: str) -> Optional[float]:
    if not OANDA_ACCOUNT_ID:
        return None
    url = f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing"
    params = {"instruments": pair}
    resp = session.get(url, params=params, timeout=3)
    resp.raise_for_status()
    prices = resp.json().get("prices", [])
    if not prices:
        return None
    p = prices[0]
    bid = float(p.get("bids", [{}])[0].get("price", 0.0))
    ask = float(p.get("asks", [{}])[0].get("price", 0.0))
    if bid and ask:
        return (bid + ask) / 2.0
    return None


def get_price_cached(pair: str) -> Tuple[Optional[float], bool]:
    now = datetime.now(timezone.utc)
    with _price_cache_lock:
        cached = _price_cache.get(pair)
        if cached:
            age = (now - cached["ts"]).total_seconds()
            if age <= PRICE_CACHE_TTL_SECONDS:
                return float(cached["price"]), False

    try:
        price = get_price(pair)
        if price is not None:
            with _price_cache_lock:
                _price_cache[pair] = {"price": float(price), "ts": now}
        return price, False
    except Exception:
        with _price_cache_lock:
            cached = _price_cache.get(pair)
            if cached:
                return float(cached["price"]), True
        return None, False


def split_completed_and_forming(candles: List[Dict]) -> Tuple[List[Dict], Optional[Dict]]:
    if not candles:
        return [], None
    completed = [c for c in candles if c.get("complete")]
    incomplete = [c for c in candles if not c.get("complete")]
    forming = incomplete[-1] if incomplete else None
    return completed, forming


def time_remaining_seconds(forming: Optional[Dict], timeframe: str) -> int:
    if not forming:
        return 0
    start = datetime.fromtimestamp(int(forming["time"]), tz=timezone.utc)
    end = start + timedelta(seconds=tf_seconds(timeframe))
    now = datetime.now(timezone.utc)
    return max(0, int((end - now).total_seconds()))


@app.route("/")
def home():
    default_pair = "EUR_USD" if "EUR_USD" in PAIRS else PAIRS[0]
    return render_template("live_chart.html", pairs=PAIRS, default_pair=default_pair)


@app.route("/api/live-candles")
def api_live_candles():
    if not OANDA_API_KEY:
        return jsonify({"ok": False, "error": "Missing OANDA_API_KEY in .env"}), 400

    requested_pair = normalize_pair(request.args.get("pair", "EUR_USD"))
    timeframe = (request.args.get("timeframe", "M15") or "M15").upper()
    if requested_pair not in PAIRS:
        requested_pair = "EUR_USD"

    available_pairs = get_available_pairs()
    effective_pair = requested_pair
    source = "oanda"
    warning = None
    if timeframe not in TIMEFRAMES:
        timeframe = "M15"

    count = int(request.args.get("count", "500"))

    try:
        candles: List[Dict]
        try:
            candles, stale_candles = get_candles_cached(
                "oanda",
                requested_pair,
                timeframe,
                count,
                lambda: get_candles(requested_pair, timeframe, count=count),
            )
            effective_pair = requested_pair
            source = "oanda"
        except Exception:
            candles, stale_candles = get_candles_cached(
                "yahoo",
                requested_pair,
                timeframe,
                count,
                lambda: get_candles_yahoo(requested_pair, timeframe, count=count),
            )
            effective_pair = requested_pair
            source = "yahoo"
            warning = f"OANDA candles unavailable for {requested_pair}. Showing Yahoo fallback feed."
        if stale_candles:
            stale_msg = "Using cached candle snapshot (network delay)."
            warning = f"{warning} {stale_msg}".strip() if warning else stale_msg

        completed, forming = split_completed_and_forming(candles)
        price = None
        stale_price = False
        if source == "oanda":
            price, stale_price = get_price_cached(effective_pair)
        if price is None:
            price = get_last_price_from_rows(candles)
        if stale_price:
            stale_msg = "Using cached quote (network delay)."
            warning = f"{warning} {stale_msg}".strip() if warning else stale_msg

        # Keep forming candle faithful to OANDA shape, only refresh latest close/high/low with live quote.
        if forming and price is not None:
            forming["close"] = float(price)
            forming["high"] = max(float(forming["high"]), float(price))
            forming["low"] = min(float(forming["low"]), float(price))

        if price is None:
            if forming:
                price = float(forming["close"])
            elif completed:
                price = float(completed[-1]["close"])
            else:
                price = 0.0

        return jsonify(
            {
                "ok": True,
                "pair": requested_pair,
                "effective_pair": effective_pair,
                "timeframe": timeframe,
                "source": source,
                "available_pairs": PAIRS,
                "tradable_pairs": available_pairs,
                "completed": completed[-500:],
                "forming": forming,
                "current_price": float(price),
                "time_remaining": time_remaining_seconds(forming, timeframe),
                "server_time": datetime.now(timezone.utc).isoformat(),
                "warning": warning,
                "latency_mode": "cached-fast",
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "pair": requested_pair, "timeframe": timeframe}), 500


if __name__ == "__main__":
    print("Live chart server: http://127.0.0.1:8050")
    app.run(host="127.0.0.1", port=8050, debug=False, threaded=True)
