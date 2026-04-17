from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Dict, List, Optional, Tuple

from broker.oanda_candles import OandaCandleFetcher
from config.settings import MIN_RR_RATIO, OANDA_ACCOUNT_ID, OANDA_API_KEY, OANDA_ENV

RSI_LENGTH = 14
RSI_UPPER_BAND = 75.0
RSI_LOWER_BAND = 25.0
RSI_SWING_MID = 50.0
SAR_START = 0.02
SAR_INCREMENT = 0.02
SAR_MAXIMUM = 0.2

FIBONACCI_RETRACEMENT_LEVELS: Dict[float, Dict[str, str]] = {
    0.000: {"label": "0%", "color": "#787B86", "style": "solid"},
    0.236: {"label": "23.6%", "color": "#787B86", "style": "dashed"},
    0.382: {"label": "38.2%", "color": "#F23645", "style": "dashed"},
    0.500: {"label": "50%", "color": "#4CAF50", "style": "solid"},
    0.618: {"label": "61.8%", "color": "#2962FF", "style": "dashed"},
    0.786: {"label": "78.6%", "color": "#9C27B0", "style": "dashed"},
    1.000: {"label": "100%", "color": "#787B86", "style": "solid"},
}

FIBONACCI_EXTENSION_LEVELS: Dict[float, Dict[str, str]] = {
    0.000: {"label": "0%", "color": "#787B86", "style": "solid"},
    0.618: {"label": "61.8%", "color": "#2962FF", "style": "dotted"},
    1.000: {"label": "100%", "color": "#787B86", "style": "solid"},
    1.272: {"label": "127.2%", "color": "#FF9800", "style": "dashed"},
    1.618: {"label": "161.8%", "color": "#E91E63", "style": "dashed"},
    2.000: {"label": "200%", "color": "#9C27B0", "style": "dashed"},
    2.618: {"label": "261.8%", "color": "#F44336", "style": "dashed"},
    3.618: {"label": "361.8%", "color": "#FF5722", "style": "dotted"},
    4.236: {"label": "423.6%", "color": "#795548", "style": "dotted"},
}


def _avg(vals: List[float], default: float = 0.0) -> float:
    v = [float(x) for x in vals if x is not None]
    return mean(v) if v else default


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    out = _avg(values[:period], values[0])
    for v in values[period:]:
        out = float(v) * k + out * (1.0 - k)
    return out


def ema_series(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    e = _avg(values[:period], values[0])
    out[period - 1] = e
    for i in range(period, len(values)):
        e = float(values[i]) * k + e * (1.0 - k)
        out[i] = e
    return out


def sma_series(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    window_sum = sum(float(v) for v in values[:period])
    out[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += float(values[i]) - float(values[i - period])
        out[i] = window_sum / period
    return out


def rma_series(values: List[float], period: int) -> List[Optional[float]]:
    """
    Wilder's moving average (TradingView ta.rma equivalent):
    seed with SMA(period), then recursive update.
    """
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = _avg(values[:period], 0.0)
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = ((prev * (period - 1)) + float(values[i])) / period
        out[i] = prev
    return out


def sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return _avg(values[-period:])


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    s = rsi_series(values, period)
    for v in reversed(s):
        if v is not None:
            return float(v)
    return None


def rsi_series(values: List[float], period: int = 14) -> List[Optional[float]]:
    """
    TradingView-aligned RSI:
    change = ta.change(src)
    up = ta.rma(max(change, 0), len)
    down = ta.rma(-min(change, 0), len)
    rsi = down == 0 ? 100 : up == 0 ? 0 : 100 - (100 / (1 + up/down))
    """
    if len(values) < period + 1:
        return [None] * len(values)

    changes: List[float] = [float(values[i]) - float(values[i - 1]) for i in range(1, len(values))]
    up_raw = [max(c, 0.0) for c in changes]
    down_raw = [-min(c, 0.0) for c in changes]
    up_rma = rma_series(up_raw, period)
    down_rma = rma_series(down_raw, period)

    out: List[Optional[float]] = [None] * len(values)
    for i in range(1, len(values)):
        up = up_rma[i - 1] if i - 1 < len(up_rma) else None
        down = down_rma[i - 1] if i - 1 < len(down_rma) else None
        if up is None or down is None:
            continue
        if down == 0:
            out[i] = 100.0
            continue
        if up == 0:
            out[i] = 0.0
            continue
        rs = float(up) / float(down)
        out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd_components(values: List[float]) -> Dict[str, Optional[float]]:
    """
    MACD module with OsMA states:
    - MACD line (12-26), signal (9), histogram/OsMA
    - histogram switch states
    - zero-line crossover states
    - OsMA rising/falling states
    """
    out: Dict[str, Optional[float]] = {
        "line": None,
        "signal": None,
        "hist": None,
        "line_prev": None,
        "signal_prev": None,
        "hist_prev": None,
        "osma": None,
        "osma_prev": None,
        "hist_switch_positive": None,
        "hist_switch_negative": None,
        "line_cross_above_zero": None,
        "line_cross_below_zero": None,
        "osma_rising": None,
        "osma_falling": None,
    }
    if len(values) < 35:
        return out

    fast = ema_series(values, 12)
    slow = ema_series(values, 26)
    macd_line_series: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if fast[i] is None or slow[i] is None:
            continue
        macd_line_series[i] = float(fast[i]) - float(slow[i])

    macd_compact: List[float] = [float(v) for v in macd_line_series if v is not None]
    signal_compact = ema_series(macd_compact, 9)
    signal_series: List[Optional[float]] = [None] * len(values)
    cidx = 0
    for i, v in enumerate(macd_line_series):
        if v is None:
            continue
        signal_series[i] = signal_compact[cidx] if cidx < len(signal_compact) else None
        cidx += 1

    hist_series: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        line = macd_line_series[i]
        sig = signal_series[i]
        if line is None or sig is None:
            continue
        hist_series[i] = float(line) - float(sig)

    valid_idx = [i for i, v in enumerate(hist_series) if v is not None]
    if not valid_idx:
        return out
    i_last = valid_idx[-1]
    i_prev = valid_idx[-2] if len(valid_idx) >= 2 else None

    line_last = macd_line_series[i_last]
    sig_last = signal_series[i_last]
    hist_last = hist_series[i_last]
    line_prev = macd_line_series[i_prev] if i_prev is not None else None
    sig_prev = signal_series[i_prev] if i_prev is not None else None
    hist_prev = hist_series[i_prev] if i_prev is not None else None

    out["line"] = line_last
    out["signal"] = sig_last
    out["hist"] = hist_last
    out["line_prev"] = line_prev
    out["signal_prev"] = sig_prev
    out["hist_prev"] = hist_prev
    out["osma"] = hist_last
    out["osma_prev"] = hist_prev

    if hist_prev is not None and hist_last is not None:
        out["hist_switch_positive"] = bool(hist_prev <= 0 < hist_last)
        out["hist_switch_negative"] = bool(hist_prev >= 0 > hist_last)
        out["osma_rising"] = bool(hist_last > hist_prev)
        out["osma_falling"] = bool(hist_last < hist_prev)

    if line_prev is not None and line_last is not None:
        out["line_cross_above_zero"] = bool(line_prev <= 0 < line_last)
        out["line_cross_below_zero"] = bool(line_prev >= 0 > line_last)

    return out


def macd_hist(values: List[float]) -> Optional[float]:
    return macd_components(values).get("hist")


def macd(values: List[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    mc = macd_components(values)
    return mc.get("line"), mc.get("signal"), mc.get("hist")


def atr(candles: List[Dict], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    tr = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return _avg(tr[-period:])


def adx(candles: List[Dict], period: int = 14) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(candles) < period + 2:
        return None, None, None

    tr: List[float] = []
    dm_plus: List[float] = []
    dm_minus: List[float] = []

    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        ph = float(candles[i - 1]["high"])
        pl = float(candles[i - 1]["low"])
        pc = float(candles[i - 1]["close"])

        up_move = h - ph
        down_move = pl - l
        plus = up_move if up_move > down_move and up_move > 0 else 0.0
        minus = down_move if down_move > up_move and down_move > 0 else 0.0

        dm_plus.append(plus)
        dm_minus.append(minus)
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(tr) < period:
        return None, None, None

    tr_s = _avg(tr[:period], 0.0)
    dp_s = _avg(dm_plus[:period], 0.0)
    dm_s = _avg(dm_minus[:period], 0.0)
    dx_vals: List[float] = []

    for i in range(period, len(tr)):
        tr_s = ((tr_s * (period - 1)) + tr[i]) / period
        dp_s = ((dp_s * (period - 1)) + dm_plus[i]) / period
        dm_s = ((dm_s * (period - 1)) + dm_minus[i]) / period
        if tr_s <= 0:
            continue
        pdi = (dp_s / tr_s) * 100.0
        mdi = (dm_s / tr_s) * 100.0
        den = pdi + mdi
        if den <= 0:
            continue
        dx_vals.append(abs(pdi - mdi) / den * 100.0)

    if not dx_vals:
        return None, None, None

    # Wilder's RMA smoothing over all DX values (matches TradingView spec).
    if len(dx_vals) < period:
        adx_val = _avg(dx_vals, 0.0)
    else:
        adx_val = _avg(dx_vals[:period], 0.0)
        for dx in dx_vals[period:]:
            adx_val = ((adx_val * (period - 1)) + dx) / period
    pdi_last = (dp_s / tr_s) * 100.0 if tr_s > 0 else None
    mdi_last = (dm_s / tr_s) * 100.0 if tr_s > 0 else None
    return adx_val, pdi_last, mdi_last


def donchian(candles: List[Dict], period: int = 20) -> Tuple[Optional[float], Optional[float]]:
    if len(candles) < period:
        return None, None
    recent = candles[-period:]
    return max(float(c["high"]) for c in recent), min(float(c["low"]) for c in recent)


def _find_pivots(
    candles: List[Dict],
    lb: int = 10,
    max_pivots: int = 20,
    max_age_bars: Optional[int] = None,
) -> Tuple[List[float], List[float]]:
    highs, lows = [], []
    if len(candles) < (2 * lb + 1):
        return highs, lows

    start = lb
    if max_age_bars is not None:
        start = max(start, len(candles) - lb - max_age_bars)

    for i in range(start, len(candles) - lb):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        neigh = candles[i - lb : i] + candles[i + 1 : i + 1 + lb]
        if all(h >= float(c["high"]) for c in neigh):
            highs.append(h)
        if all(l <= float(c["low"]) for c in neigh):
            lows.append(l)
    if max_pivots > 0:
        highs = highs[-max_pivots:]
        lows = lows[-max_pivots:]
    return highs, lows


def _cluster(levels: List[float], width: float, min_strength: int = 2, max_levels: int = 5) -> List[Dict]:
    out: List[List[float]] = []
    for lv in sorted(levels):
        placed = False
        for b in out:
            if abs(lv - _avg(b)) <= width:
                b.append(lv)
                placed = True
                break
        if not placed:
            out.append([lv])
    rows = [{"level": round(_avg(b), 6), "touches": len(b)} for b in out if len(b) >= min_strength]
    rows.sort(key=lambda x: x["touches"], reverse=True)
    return rows[:max_levels]


def _nearest(levels: List[Dict], price: float) -> Optional[Dict]:
    return min(levels, key=lambda z: abs(float(z["level"]) - price)) if levels else None


def _pip(pair: str) -> float:
    p = pair.upper()
    if p.endswith("JPY"):
        return 0.01
    if p.startswith("XAU") or p.startswith("XAG"):
        return 0.1
    if p.startswith("BTC"):
        return 1.0
    return 0.0001


def _round_step(pair: str, price: float) -> float:
    p = _pip(pair)
    if p >= 1.0:
        return 10.0 if abs(price) < 10000 else 100.0
    if p >= 0.1:
        return 1.0
    if p >= 0.01:
        return 0.1
    return 0.001


def _harden_stop_loss(
    sl: float,
    pair: str,
    direction: str,
    atr_val: float,
    sr_level: Optional[float] = None,
    zone_low: Optional[float] = None,
    zone_high: Optional[float] = None,
    flip_level: Optional[float] = None,
) -> Tuple[float, List[str]]:
    """
    Institutional SL protection:
    - keep SL away from exact S/R lines
    - keep SL away from obvious round numbers
    - add anti-stop-hunt cushion beyond key liquidity levels
    """
    out = float(sl)
    notes: List[str] = []
    pip = _pip(pair)
    base_buf = max(float(atr_val) * 0.18, pip * 6)

    def move_away(ref: float):
        nonlocal out
        if direction == "BUY":
            target = float(ref) - base_buf
            if out > target:
                out = target
        else:
            target = float(ref) + base_buf
            if out < target:
                out = target

    if sr_level is not None and abs(out - float(sr_level)) <= base_buf:
        move_away(float(sr_level))
        notes.append("SL shifted away from exact S/R level.")

    if direction == "BUY" and zone_low is not None:
        if out > float(zone_low) - base_buf:
            out = float(zone_low) - base_buf
            notes.append("SL moved below demand/support zone with institutional buffer.")
    if direction == "SELL" and zone_high is not None:
        if out < float(zone_high) + base_buf:
            out = float(zone_high) + base_buf
            notes.append("SL moved above supply/resistance zone with institutional buffer.")

    if flip_level is not None:
        move_away(float(flip_level))
        notes.append("SL buffered beyond flipped level to avoid stop-hunt sweep.")

    step = _round_step(pair, out)
    round_lv = round(out / step) * step
    if abs(out - round_lv) <= (pip * 2):
        out = out - (pip * 4) if direction == "BUY" else out + (pip * 4)
        notes.append("SL moved away from obvious round number.")

    return out, notes


def smma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    val = _avg(values[:period], 0.0)
    for v in values[period:]:
        val = ((val * (period - 1)) + float(v)) / period
    return val


def _tf_trend(candles: List[Dict], ema_period: int = 50) -> str:
    if len(candles) < ema_period:
        return "neutral"
    closes = [float(c["close"]) for c in candles]
    ev = ema(closes, ema_period)
    if ev is None:
        return "neutral"
    if closes[-1] > ev:
        return "bullish"
    if closes[-1] < ev:
        return "bearish"
    return "neutral"


def parabolic_sar_signal(
    candles: List[Dict], start: float = 0.02, increment: float = 0.02, maximum: float = 0.2
) -> Tuple[Optional[str], Optional[float], int]:
    if len(candles) < 4:
        return None, None, 0

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]

    bull = closes[1] >= closes[0]
    af = start
    ep = highs[0] if bull else lows[0]
    sar = lows[0] if bull else highs[0]
    history: List[str] = []

    for i in range(1, len(candles)):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, lows[i - 1], lows[i - 2] if i > 1 else lows[i - 1])
            if lows[i] < sar:
                bull = False
                sar = ep
                ep = lows[i]
                af = start
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(maximum, af + increment)
        else:
            sar = max(sar, highs[i - 1], highs[i - 2] if i > 1 else highs[i - 1])
            if highs[i] > sar:
                bull = True
                sar = ep
                ep = highs[i]
                af = start
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(maximum, af + increment)
        history.append("bullish" if bull else "bearish")

    side = history[-1] if history else None
    streak = 0
    for s in reversed(history):
        if s == side:
            streak += 1
        else:
            break
    return side, sar, streak


def _find_divergence(candles: List[Dict], rsi_vals: List[Optional[float]]) -> str:
    """
    TradingView-style RSI divergence approximation aligned with the user-provided Pine logic:
      - lookbackLeft = 5, lookbackRight = 5
      - rangeLower = 5, rangeUpper = 60
      - pivot detection on RSI (not price)
      - valuewhen(plFound/phFound, source, 1) behavior for prior pivot comparison
      - barssince(plFound[1]/phFound[1]) in-range gating
    """
    n = len(candles)
    if n < 40 or n != len(rsi_vals):
        return "none"

    lookback_left = 5
    lookback_right = 5
    range_lower = 5
    range_upper = 60
    # Keep divergence "live" for a short window so it can act as warning context.
    signal_life_bars = 20

    def _bars_since(flags: List[bool]) -> List[Optional[int]]:
        out: List[Optional[int]] = [None] * len(flags)
        last_true: Optional[int] = None
        for i, f in enumerate(flags):
            if f:
                last_true = i
                out[i] = 0
            elif last_true is not None:
                out[i] = i - last_true
        return out

    def _in_range(bars: Optional[int]) -> bool:
        return bars is not None and range_lower <= int(bars) <= range_upper

    pl_found = [False] * n
    ph_found = [False] * n

    # Mirror ta.pivotlow/ta.pivothigh confirmation timing:
    # at bar t, pivot point is at t-lookback_right.
    start_t = lookback_left + lookback_right
    for t in range(start_t, n):
        p = t - lookback_right
        center = rsi_vals[p]
        if center is None:
            continue
        window = rsi_vals[p - lookback_left : p + lookback_right + 1]
        if len(window) != (lookback_left + lookback_right + 1):
            continue
        if any(v is None for v in window):
            continue
        cv = float(center)
        if all(cv <= float(v) for v in window):
            pl_found[t] = True
        if all(cv >= float(v) for v in window):
            ph_found[t] = True

    # Pine uses _inRange(plFound[1]) and _inRange(phFound[1])
    # so we shift by one bar before barssince.
    pl_shift = [False] + pl_found[:-1]
    ph_shift = [False] + ph_found[:-1]
    pl_bars_since = _bars_since(pl_shift)
    ph_bars_since = _bars_since(ph_shift)

    rsi_lbr: List[Optional[float]] = [None] * n
    low_lbr: List[Optional[float]] = [None] * n
    high_lbr: List[Optional[float]] = [None] * n
    for t in range(lookback_right, n):
        rsi_lbr[t] = rsi_vals[t - lookback_right]
        low_lbr[t] = float(candles[t - lookback_right]["low"])
        high_lbr[t] = float(candles[t - lookback_right]["high"])

    bull_cond = [False] * n
    bear_cond = [False] * n

    prev_pl_rsi: Optional[float] = None
    prev_pl_low: Optional[float] = None
    prev_ph_rsi: Optional[float] = None
    prev_ph_high: Optional[float] = None

    for t in range(n):
        if pl_found[t]:
            curr_rsi = rsi_lbr[t]
            curr_low = low_lbr[t]
            if curr_rsi is not None and curr_low is not None:
                rsi_hl = (
                    prev_pl_rsi is not None
                    and float(curr_rsi) > float(prev_pl_rsi)
                    and _in_range(pl_bars_since[t])
                )
                price_ll = prev_pl_low is not None and float(curr_low) < float(prev_pl_low)
                bull_cond[t] = bool(price_ll and rsi_hl)
                prev_pl_rsi = float(curr_rsi)
                prev_pl_low = float(curr_low)

        if ph_found[t]:
            curr_rsi = rsi_lbr[t]
            curr_high = high_lbr[t]
            if curr_rsi is not None and curr_high is not None:
                rsi_lh = (
                    prev_ph_rsi is not None
                    and float(curr_rsi) < float(prev_ph_rsi)
                    and _in_range(ph_bars_since[t])
                )
                price_hh = prev_ph_high is not None and float(curr_high) > float(prev_ph_high)
                bear_cond[t] = bool(price_hh and rsi_lh)
                prev_ph_rsi = float(curr_rsi)
                prev_ph_high = float(curr_high)

    latest_type = "none"
    latest_idx: Optional[int] = None
    for t in range(n - 1, -1, -1):
        if bull_cond[t] and not bear_cond[t]:
            latest_type = "bullish"
            latest_idx = t
            break
        if bear_cond[t] and not bull_cond[t]:
            latest_type = "bearish"
            latest_idx = t
            break

    if latest_idx is None:
        return "none"
    if (n - 1 - latest_idx) > signal_life_bars:
        return "none"
    return latest_type


def _candlestick_signal(candles: List[Dict]) -> Dict:
    if len(candles) < 2:
        return {
            "name": "none",
            "bullish": False,
            "bearish": False,
            "strength": 0.0,
            "requires_confirmation": False,
            "indecision": False,
            "no_top_wick_note": False,
            "all_matches": [],
        }

    c = candles[-1]
    p = candles[-2]
    c3 = candles[-3] if len(candles) >= 3 else None
    c4 = candles[-4] if len(candles) >= 4 else None

    o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
    po, ph, pl, pc = float(p["open"]), float(p["high"]), float(p["low"]), float(p["close"])

    body = abs(cl - o)
    rng = max(h - l, 1e-9)
    upper = h - max(o, cl)
    lower = min(o, cl) - l
    body_ratio = body / rng
    is_bull = cl > o
    is_bear = cl < o
    doji = body <= rng * 0.10
    no_top_wick = upper <= rng * 0.05
    both_wicks_indecision = upper >= rng * 0.25 and lower >= rng * 0.25 and body_ratio <= 0.30

    recent3 = candles[-4:-1] if len(candles) >= 4 else candles[:-1]
    up_context = False
    down_context = False
    if len(recent3) >= 2:
        seq_closes = [float(x["close"]) for x in recent3]
        up_context = seq_closes[-1] > seq_closes[0]
        down_context = seq_closes[-1] < seq_closes[0]

    matches: List[Dict] = []

    def add(name: str, direction: str, strength: float, requires_confirmation: bool = False):
        matches.append(
            {
                "name": name,
                "direction": direction,
                "strength": strength,
                "requires_confirmation": requires_confirmation,
            }
        )

    # Bullish patterns.
    hammer = lower >= body * 2.0 and upper <= max(body * 0.6, rng * 0.12)
    inverted_hammer = upper >= body * 2.0 and lower <= max(body * 0.6, rng * 0.12)
    bullish_doji = doji and cl >= o
    dragonfly_doji = doji and lower >= rng * 0.45 and upper <= rng * 0.10
    hanging_man_bottom = hammer and down_context
    bullish_engulfing = is_bull and pc < po and cl >= po and o <= pc
    tweezer_bottom = abs(l - pl) <= rng * 0.12 and is_bull and pc < po
    big_green = is_bull and body_ratio >= 0.60 and upper <= rng * 0.08

    if len(candles) >= 4 and c3 is not None and c4 is not None:
        c2 = p
        c1 = c4
        c2b = c3
        c3b = c2
        c4b = c
        bearish_run = (
            float(c1["close"]) < float(c1["open"])
            and float(c2b["close"]) < float(c2b["open"])
            and float(c3b["close"]) < float(c3b["open"])
            and float(c3b["close"]) < float(c2b["close"]) < float(c1["close"])
        )
        strike_bull = (
            bearish_run
            and float(c4b["close"]) > float(c4b["open"])
            and float(c4b["close"]) >= float(c1["open"])
        )
        if strike_bull:
            add("three_line_strike_bullish", "bullish", 3.0, requires_confirmation=False)

    if hammer:
        add("hammer", "bullish", 3.0, requires_confirmation=False)
    if inverted_hammer:
        add("inverted_hammer", "bullish", 2.2, requires_confirmation=True)
    if bullish_doji:
        add("bullish_doji", "bullish", 2.0, requires_confirmation=True)
    if dragonfly_doji:
        add("dragonfly_doji", "bullish", 3.0, requires_confirmation=False)
    if hanging_man_bottom:
        add("hanging_man_bottom", "bullish", 2.4, requires_confirmation=False)
    if bullish_engulfing:
        add("bullish_engulfing", "bullish", 3.0, requires_confirmation=False)
    if tweezer_bottom:
        add("tweezer_bottom", "bullish", 2.6, requires_confirmation=False)
    if big_green:
        add("big_green_no_tiny_wick", "bullish", 2.3, requires_confirmation=False)

    # Bearish patterns.
    bearish_doji = doji and cl < o
    gravestone_doji = doji and upper >= rng * 0.45 and lower <= rng * 0.10
    rickshaw_man = doji and upper >= rng * 0.30 and lower >= rng * 0.30
    bearish_engulfing = is_bear and pc > po and o >= pc and cl <= po
    tweezer_top = abs(h - ph) <= rng * 0.12 and is_bear and pc > po
    big_red = is_bear and body_ratio >= 0.60 and upper <= rng * 0.08

    if len(candles) >= 4 and c3 is not None and c4 is not None:
        c2 = p
        c1 = c4
        c2b = c3
        c3b = c2
        c4b = c
        bullish_run = (
            float(c1["close"]) > float(c1["open"])
            and float(c2b["close"]) > float(c2b["open"])
            and float(c3b["close"]) > float(c3b["open"])
            and float(c3b["close"]) > float(c2b["close"]) > float(c1["close"])
        )
        strike_bear = (
            bullish_run
            and float(c4b["close"]) < float(c4b["open"])
            and float(c4b["close"]) <= float(c1["open"])
        )
        if strike_bear:
            add("three_line_strike_bearish", "bearish", 3.0, requires_confirmation=False)

    if bearish_doji:
        add("bearish_doji", "bearish", 2.0, requires_confirmation=True)
    if gravestone_doji:
        add("gravestone_doji", "bearish", 3.0, requires_confirmation=False)
    if rickshaw_man:
        add("rickshaw_man", "bearish", 2.2, requires_confirmation=False)
    if bearish_engulfing:
        add("bearish_engulfing", "bearish", 3.0, requires_confirmation=False)
    if tweezer_top:
        add("tweezer_top", "bearish", 2.6, requires_confirmation=False)
    if big_red:
        add("big_red_no_tiny_wick", "bearish", 2.3, requires_confirmation=False)

    confirmed_matches: List[Dict] = []
    provisional_matches: List[Dict] = []
    for m in matches:
        if not m["requires_confirmation"]:
            confirmed_matches.append(m)
            continue
        if m["direction"] == "bullish":
            confirmed = is_bull and cl > pc
        else:
            confirmed = is_bear and cl < pc
        if confirmed:
            cm = dict(m)
            cm["requires_confirmation"] = False
            confirmed_matches.append(cm)
        else:
            provisional_matches.append(m)

    candidates = confirmed_matches or provisional_matches
    if not candidates:
        return {
            "name": "indecision" if both_wicks_indecision else "none",
            "bullish": False,
            "bearish": False,
            "strength": 0.0,
            "requires_confirmation": False,
            "indecision": both_wicks_indecision,
            "no_top_wick_note": bool(no_top_wick and body_ratio >= 0.60),
            "all_matches": [],
        }

    best = sorted(
        candidates,
        key=lambda x: (x["strength"], 0 if not x["requires_confirmation"] else -0.2),
        reverse=True,
    )[0]
    direction = str(best["direction"])
    return {
        "name": str(best["name"]),
        "bullish": direction == "bullish",
        "bearish": direction == "bearish",
        "strength": float(best["strength"]),
        "requires_confirmation": bool(best["requires_confirmation"]),
        "indecision": both_wicks_indecision,
        "no_top_wick_note": bool(no_top_wick and body_ratio >= 0.60),
        "all_matches": [m["name"] for m in candidates],
    }


def _detect_fvg(candles: List[Dict]) -> str:
    if len(candles) < 3:
        return "none"
    c1 = candles[-3]
    c3 = candles[-1]
    if float(c1["high"]) < float(c3["low"]):
        return "bullish"
    if float(c1["low"]) > float(c3["high"]):
        return "bearish"
    return "none"


def _series_slope(values: List[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = _avg(values, 0.0)
    num = 0.0
    den = 0.0
    for i, v in enumerate(values):
        dx = float(i) - x_mean
        num += dx * (float(v) - y_mean)
        den += dx * dx
    if den <= 0:
        return 0.0
    return num / den


def _pivot_points(
    candles: List[Dict], lb: int = 3, max_points: int = 24
) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    highs: List[Tuple[int, float]] = []
    lows: List[Tuple[int, float]] = []
    if len(candles) < (2 * lb + 1):
        return highs, lows
    for i in range(lb, len(candles) - lb):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        neigh = candles[i - lb : i] + candles[i + 1 : i + 1 + lb]
        if all(h >= float(c["high"]) for c in neigh):
            highs.append((i, h))
        if all(l <= float(c["low"]) for c in neigh):
            lows.append((i, l))
    if max_points > 0:
        highs = highs[-max_points:]
        lows = lows[-max_points:]
    return highs, lows


def _latest_equal_cluster(points: List[Tuple[int, float]], tol: float) -> Optional[Dict]:
    if len(points) < 2:
        return None
    for i in range(len(points) - 1, 0, -1):
        base_idx, base_val = points[i]
        cluster: List[Tuple[int, float]] = [(base_idx, base_val)]
        for j in range(i - 1, -1, -1):
            idx, val = points[j]
            if abs(float(val) - float(base_val)) <= tol:
                cluster.append((idx, val))
            if len(cluster) >= 4:
                break
        if len(cluster) >= 2:
            cluster = sorted(cluster, key=lambda t: t[0])
            vals = [float(v) for _, v in cluster]
            return {
                "level": round(_avg(vals, 0.0), 6),
                "touches": len(cluster),
                "first_index": int(cluster[0][0]),
                "last_index": int(cluster[-1][0]),
            }
    return None


def _detect_order_blocks(candles: List[Dict], pair: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    if len(candles) < 30:
        return None, None
    lookback = min(140, len(candles))
    start = len(candles) - lookback
    view = candles[start:]
    a = atr(view, 14) or (_pip(pair) * 20)
    bullish_ob: Optional[Dict] = None
    bearish_ob: Optional[Dict] = None
    for i in range(0, len(view) - 4):
        c = view[i]
        o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
        body = abs(cl - o)
        if body < a * 0.20:
            continue
        f1 = float(view[i + 1]["close"])
        f2 = float(view[i + 2]["close"])
        f3 = float(view[i + 3]["close"])
        impulse_up = (f3 - cl) >= a * 0.9 and f3 > f2 > f1
        impulse_down = (cl - f3) >= a * 0.9 and f3 < f2 < f1
        if cl < o and impulse_up:
            strength = min(3.0, max(0.1, (f3 - cl) / max(a, 1e-9)))
            bullish_ob = {
                "low": round(l, 6),
                "high": round(max(o, cl), 6),
                "time": c.get("time"),
                "index": start + i,
                "strength": round(strength, 3),
            }
        if cl > o and impulse_down:
            strength = min(3.0, max(0.1, (cl - f3) / max(a, 1e-9)))
            bearish_ob = {
                "low": round(min(o, cl), 6),
                "high": round(h, 6),
                "time": c.get("time"),
                "index": start + i,
                "strength": round(strength, 3),
            }
    return bullish_ob, bearish_ob


def _premium_discount_context(candles: List[Dict], price: float) -> Dict:
    if not candles:
        return {
            "range_high": None,
            "range_low": None,
            "equilibrium": None,
            "premium_zone": False,
            "discount_zone": False,
            "bias_zone_alignment": False,
        }
    look = min(120, len(candles))
    recent = candles[-look:]
    r_high = max(float(c["high"]) for c in recent)
    r_low = min(float(c["low"]) for c in recent)
    eq = (r_high + r_low) / 2.0
    return {
        "range_high": round(r_high, 6),
        "range_low": round(r_low, 6),
        "equilibrium": round(eq, 6),
        "premium_zone": bool(price > eq),
        "discount_zone": bool(price < eq),
    }


def _smc_context(
    candles: List[Dict],
    pair: str,
    bias: str,
    structure: Dict,
    liquidity: Dict,
    fvg: str,
    in_demand: bool,
    in_supply: bool,
    tol: float,
) -> Dict:
    price = float(candles[-1]["close"]) if candles else 0.0
    a = atr(candles, 14) or (_pip(pair) * 20)
    bull_ob, bear_ob = _detect_order_blocks(candles, pair)
    in_bull_ob = _zone_hit(price, bull_ob, tol) if bull_ob else False
    in_bear_ob = _zone_hit(price, bear_ob, tol) if bear_ob else False
    piv_highs, piv_lows = _pivot_points(candles, lb=3, max_points=22)
    eq_tol = max(a * 0.18, _pip(pair) * 6)
    eq_highs = _latest_equal_cluster(piv_highs, eq_tol)
    eq_lows = _latest_equal_cluster(piv_lows, eq_tol)
    near_eq_highs = bool(eq_highs and abs(price - float(eq_highs["level"])) <= (tol * 1.6))
    near_eq_lows = bool(eq_lows and abs(price - float(eq_lows["level"])) <= (tol * 1.6))
    pd = _premium_discount_context(candles, price)

    choch = str(structure.get("choch") or "none")
    bos = str(structure.get("bos") or "none")
    buy_sweep = bool(liquidity.get("buy_side_sweep"))
    sell_sweep = bool(liquidity.get("sell_side_sweep"))
    buy_stop_hunt = bool(liquidity.get("buy_side_stop_hunt"))
    sell_stop_hunt = bool(liquidity.get("sell_side_stop_hunt"))

    bull_factors = {
        "structure_shift": choch == "bullish" or bos == "bullish",
        "order_block": bool(in_bull_ob or in_demand),
        "fvg": fvg == "bullish",
        "liquidity": bool(sell_sweep or sell_stop_hunt or near_eq_lows),
        "discount": bool(pd.get("discount_zone")),
    }
    bear_factors = {
        "structure_shift": choch == "bearish" or bos == "bearish",
        "order_block": bool(in_bear_ob or in_supply),
        "fvg": fvg == "bearish",
        "liquidity": bool(buy_sweep or buy_stop_hunt or near_eq_highs),
        "premium": bool(pd.get("premium_zone")),
    }
    bull_score = sum(1 for v in bull_factors.values() if v)
    bear_score = sum(1 for v in bear_factors.values() if v)
    bias_zone_alignment = bool(
        (bias == "bullish" and pd.get("discount_zone"))
        or (bias == "bearish" and pd.get("premium_zone"))
    )

    smc_signal_aligned = False
    if bias == "bullish":
        smc_signal_aligned = bull_score >= 2
    elif bias == "bearish":
        smc_signal_aligned = bear_score >= 2
    else:
        smc_signal_aligned = max(bull_score, bear_score) >= 3

    reasons: List[str] = []
    active = bull_factors if bias == "bullish" else bear_factors if bias == "bearish" else {}
    for name, ok in active.items():
        if ok:
            reasons.append(name.replace("_", " "))
    if bias not in ("bullish", "bearish"):
        if bull_score >= 3:
            reasons.append("bullish smc cluster")
        if bear_score >= 3:
            reasons.append("bearish smc cluster")

    return {
        "bullish_order_block": bull_ob,
        "bearish_order_block": bear_ob,
        "in_bullish_order_block": in_bull_ob,
        "in_bearish_order_block": in_bear_ob,
        "equal_highs": eq_highs,
        "equal_lows": eq_lows,
        "near_equal_highs": near_eq_highs,
        "near_equal_lows": near_eq_lows,
        "range_high": pd.get("range_high"),
        "range_low": pd.get("range_low"),
        "equilibrium": pd.get("equilibrium"),
        "premium_zone": bool(pd.get("premium_zone")),
        "discount_zone": bool(pd.get("discount_zone")),
        "bias_zone_alignment": bias_zone_alignment,
        "choch": choch,
        "bos": bos,
        "buy_side_sweep": buy_sweep,
        "sell_side_sweep": sell_sweep,
        "buy_side_stop_hunt": buy_stop_hunt,
        "sell_side_stop_hunt": sell_stop_hunt,
        "bullish_score": bull_score,
        "bearish_score": bear_score,
        "smc_signal_aligned": smc_signal_aligned,
        "reasons": reasons,
    }


def _detect_chart_patterns(candles: List[Dict], pair: str) -> Dict:
    out = {
        "primary": "none",
        "direction": "neutral",
        "strength": 0.0,
        "aligned_with_bias": False,
        "matches": [],
    }
    n = len(candles)
    if n < 30:
        return out

    closes = [float(c["close"]) for c in candles]
    highs_series = [float(c["high"]) for c in candles]
    lows_series = [float(c["low"]) for c in candles]
    price = closes[-1]
    a = atr(candles, 14) or (_pip(pair) * 20)
    tol = max(a * 0.35, _pip(pair) * 8)
    piv_highs, piv_lows = _pivot_points(candles, lb=3, max_points=30)

    matches: List[Dict] = []

    def add(name: str, direction: str, strength: float, confirmed: bool = False):
        matches.append(
            {
                "name": name,
                "direction": direction,
                "strength": float(strength),
                "confirmed": bool(confirmed),
            }
        )

    # Double top / bottom.
    if len(piv_highs) >= 2:
        (i1, h1), (i2, h2) = piv_highs[-2], piv_highs[-1]
        if i2 - i1 >= 4 and abs(h1 - h2) <= tol:
            neckline = min(float(c["low"]) for c in candles[i1 : i2 + 1])
            confirmed = price < neckline
            add("double_top", "bearish", 0.72 if confirmed else 0.62, confirmed)
    if len(piv_lows) >= 2:
        (i1, l1), (i2, l2) = piv_lows[-2], piv_lows[-1]
        if i2 - i1 >= 4 and abs(l1 - l2) <= tol:
            neckline = max(float(c["high"]) for c in candles[i1 : i2 + 1])
            confirmed = price > neckline
            add("double_bottom", "bullish", 0.72 if confirmed else 0.62, confirmed)

    # Head & shoulders / inverse.
    if len(piv_highs) >= 3:
        (li, lh), (hi, hh), (ri, rh) = piv_highs[-3], piv_highs[-2], piv_highs[-1]
        shoulders_balanced = abs(lh - rh) <= (tol * 1.4)
        head_above = hh > lh + (tol * 0.8) and hh > rh + (tol * 0.8)
        if li < hi < ri and shoulders_balanced and head_above:
            neckline = min(float(c["low"]) for c in candles[li : ri + 1])
            confirmed = price < neckline
            add("head_and_shoulders", "bearish", 0.82 if confirmed else 0.68, confirmed)
    if len(piv_lows) >= 3:
        (li, ll), (hi, hl), (ri, rl) = piv_lows[-3], piv_lows[-2], piv_lows[-1]
        shoulders_balanced = abs(ll - rl) <= (tol * 1.4)
        head_below = hl < ll - (tol * 0.8) and hl < rl - (tol * 0.8)
        if li < hi < ri and shoulders_balanced and head_below:
            neckline = max(float(c["high"]) for c in candles[li : ri + 1])
            confirmed = price > neckline
            add("inverse_head_and_shoulders", "bullish", 0.82 if confirmed else 0.68, confirmed)

    # Triangles.
    win = min(35, n)
    hi_recent = [p for p in piv_highs if p[0] >= n - win]
    lo_recent = [p for p in piv_lows if p[0] >= n - win]
    if len(hi_recent) >= 2 and len(lo_recent) >= 2:
        hvals = [float(v) for _, v in hi_recent[-3:]]
        lvals = [float(v) for _, v in lo_recent[-3:]]
        high_flat = (max(hvals) - min(hvals)) <= (tol * 1.6)
        low_flat = (max(lvals) - min(lvals)) <= (tol * 1.6)
        low_rising = (lvals[-1] - lvals[0]) > (tol * 0.8)
        high_falling = (hvals[0] - hvals[-1]) > (tol * 0.8)
        if high_flat and low_rising:
            add("ascending_triangle", "bullish", 0.58, False)
        if low_flat and high_falling:
            add("descending_triangle", "bearish", 0.58, False)

    # Flags.
    if n >= 45:
        pole_start = n - 38
        pole_end = n - 20
        cons = closes[-20:]
        pole_move = float(closes[pole_end]) - float(closes[pole_start])
        cons_slope = _series_slope(cons)
        cons_range = max(cons) - min(cons)
        if abs(pole_move) >= (a * 4.0) and cons_range <= (abs(pole_move) * 0.60):
            if pole_move > 0 and cons_slope < 0:
                add("bull_flag", "bullish", 0.57, False)
            if pole_move < 0 and cons_slope > 0:
                add("bear_flag", "bearish", 0.57, False)

    # Wedges.
    if n >= 35:
        hs = highs_series[-30:]
        ls = lows_series[-30:]
        hslope = _series_slope(hs)
        lslope = _series_slope(ls)
        spread_start = hs[0] - ls[0]
        spread_end = hs[-1] - ls[-1]
        converging = spread_start > 0 and spread_end < (spread_start * 0.85)
        if converging and hslope > 0 and lslope > 0:
            add("rising_wedge", "bearish", 0.60, False)
        if converging and hslope < 0 and lslope < 0:
            add("falling_wedge", "bullish", 0.60, False)

    # Cup & handle (approximation).
    if n >= 70:
        seg = candles[-70:]
        seg_highs = [float(c["high"]) for c in seg]
        seg_lows = [float(c["low"]) for c in seg]
        left_peak_i = max(range(0, 22), key=lambda i: seg_highs[i])
        right_window_start = 48
        right_peak_i_rel = max(range(0, 22), key=lambda i: seg_highs[right_window_start + i])
        right_peak_i = right_window_start + right_peak_i_rel
        if right_peak_i > left_peak_i + 15:
            bottom_i = min(range(left_peak_i, right_peak_i + 1), key=lambda i: seg_lows[i])
            left_peak = seg_highs[left_peak_i]
            right_peak = seg_highs[right_peak_i]
            bottom = seg_lows[bottom_i]
            cup_depth = min(left_peak, right_peak) - bottom
            rim_diff = abs(left_peak - right_peak)
            if cup_depth >= (a * 2.5) and rim_diff <= max(tol * 2.0, cup_depth * 0.35):
                handle_slice = seg[-10:]
                handle_high = max(float(c["high"]) for c in handle_slice)
                handle_low = min(float(c["low"]) for c in handle_slice)
                handle_drop = handle_high - handle_low
                if handle_drop <= (cup_depth * 0.45):
                    add("cup_and_handle", "bullish", 0.64, False)

    if not matches:
        return out

    ranked = sorted(matches, key=lambda m: (m["strength"], 1 if m["confirmed"] else 0), reverse=True)
    primary = ranked[0]
    out["primary"] = str(primary["name"])
    out["direction"] = str(primary["direction"])
    out["strength"] = round(float(primary["strength"]), 3)
    out["matches"] = ranked
    return out


def _detect_supply_demand_zones(
    candles: List[Dict], pair: str, tf_label: str = "4H"
) -> Tuple[List[Dict], List[Dict]]:
    if len(candles) < 25:
        return [], []

    a = atr(candles, 14) or (_pip(pair) * 20)
    supply: List[Dict] = []
    demand: List[Dict] = []
    start = max(2, len(candles) - 100)

    for i in range(start, len(candles) - 3):
        c = candles[i]
        o, h, l, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
        body = abs(cl - o)
        rng = max(h - l, 1e-9)
        body_ratio = body / rng
        upper_wick = h - max(o, cl)
        lower_wick = min(o, cl) - l

        if body < a * 0.35:
            continue
        next_close = float(candles[i + 3]["close"])
        rise = next_close - cl
        fall = cl - next_close

        tf_weight = 1.35 if tf_label == "4H" else 1.0
        departure_up = max(0.0, rise / max(a, 1e-9))
        departure_down = max(0.0, fall / max(a, 1e-9))
        wick_reject_demand = lower_wick / rng >= 0.22
        wick_reject_supply = upper_wick / rng >= 0.22
        strong_body = body_ratio >= 0.42

        if cl < o and rise >= a * 0.8 and (strong_body or wick_reject_demand):
            score = tf_weight * (0.55 * min(2.0, departure_up) + 0.30 * min(1.5, body_ratio) + 0.15 * (1.0 if wick_reject_demand else 0.0))
            demand.append(
                {
                    "low": round(l, 6),
                    "high": round(max(o, cl), 6),
                    "time": c.get("time"),
                    "tf": tf_label,
                    "wick_rejection": wick_reject_demand,
                    "body_ratio": round(body_ratio, 3),
                    "departure_strength": round(departure_up, 3),
                    "score": round(score, 3),
                }
            )
        if cl > o and fall >= a * 0.8 and (strong_body or wick_reject_supply):
            score = tf_weight * (0.55 * min(2.0, departure_down) + 0.30 * min(1.5, body_ratio) + 0.15 * (1.0 if wick_reject_supply else 0.0))
            supply.append(
                {
                    "low": round(min(o, cl), 6),
                    "high": round(h, 6),
                    "time": c.get("time"),
                    "tf": tf_label,
                    "wick_rejection": wick_reject_supply,
                    "body_ratio": round(body_ratio, 3),
                    "departure_strength": round(departure_down, 3),
                    "score": round(score, 3),
                }
            )

    supply = sorted(supply, key=lambda z: z.get("score", 0.0), reverse=True)
    demand = sorted(demand, key=lambda z: z.get("score", 0.0), reverse=True)
    return supply[:8], demand[:8]


def _zone_hit(price: float, zone: Optional[Dict], tol: float) -> bool:
    if not zone:
        return False
    low = float(zone["low"]) - tol
    high = float(zone["high"]) + tol
    return low <= price <= high


def _nearest_zone_with_priority(zones: List[Dict], price: float) -> Optional[Dict]:
    if not zones:
        return None

    def rank(z: Dict):
        mid = _avg([float(z["low"]), float(z["high"])], price)
        dist = abs(price - mid)
        tf_boost = 0.0
        tf = str(z.get("tf", "")).upper()
        if tf == "4H":
            tf_boost = 0.30
        elif tf == "1H":
            tf_boost = 0.05
        score = float(z.get("score", 0.0)) + tf_boost
        return (dist, -score)

    return sorted(zones, key=rank)[0]


def _zone_wick_rejection(candle: Dict, zone: Optional[Dict], direction: str, tol: float) -> bool:
    if not zone:
        return False
    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    c = float(candle["close"])
    low = float(zone["low"])
    high = float(zone["high"])
    body = abs(c - o)

    if direction == "bullish":
        # Price enters demand zone and rejects with lower wick.
        touched = l <= (high + tol) and l >= (low - 2.0 * tol)
        reject = c >= (low + tol * 0.25) and (min(o, c) - l) >= body * 0.6
        return touched and reject

    touched = h >= (low - tol) and h <= (high + 2.0 * tol)
    reject = c <= (high - tol * 0.25) and (h - max(o, c)) >= body * 0.6
    return touched and reject


def _fib_levels_to_dict(levels: Dict[float, float]) -> Dict[str, float]:
    return {f"{float(k):.3f}": round(float(v), 6) for k, v in levels.items()}


def _detect_swing_highs(candles: List[Dict], lookback: int = 10, min_swing_pct: float = 0.5) -> List[Dict]:
    swings: List[Dict] = []
    n = len(candles)
    if n < (lookback * 2 + 5):
        return swings
    for i in range(lookback, n - lookback):
        current_high = float(candles[i]["high"])

        is_swing = True
        for j in range(i - lookback, i):
            if float(candles[j]["high"]) >= current_high:
                is_swing = False
                break
        if not is_swing:
            continue
        for j in range(i + 1, min(i + lookback + 1, n)):
            if float(candles[j]["high"]) >= current_high:
                is_swing = False
                break
        if not is_swing:
            continue

        surrounding = []
        for j in range(max(0, i - lookback * 2), min(n, i + lookback * 2 + 1)):
            if j == i:
                continue
            surrounding.append(float(candles[j]["high"]))
        avg_surrounding = _avg(surrounding, current_high)
        strength = (current_high - avg_surrounding) / max(avg_surrounding, 1e-9)

        local_low = min(float(candles[j]["low"]) for j in range(max(0, i - lookback), min(n, i + lookback + 1)))
        move_pct = ((current_high - local_low) / max(local_low, 1e-9)) * 100.0
        if move_pct < float(min_swing_pct):
            continue

        swings.append(
            {
                "index": i,
                "price": current_high,
                "time": candles[i].get("time"),
                "strength": round(min(max(strength * 100.0, 0.0), 1.0), 3),
                "move_pct": round(move_pct, 3),
            }
        )
    return swings


def _detect_swing_lows(candles: List[Dict], lookback: int = 10, min_swing_pct: float = 0.5) -> List[Dict]:
    swings: List[Dict] = []
    n = len(candles)
    if n < (lookback * 2 + 5):
        return swings
    for i in range(lookback, n - lookback):
        current_low = float(candles[i]["low"])

        is_swing = True
        for j in range(i - lookback, i):
            if float(candles[j]["low"]) <= current_low:
                is_swing = False
                break
        if not is_swing:
            continue
        for j in range(i + 1, min(i + lookback + 1, n)):
            if float(candles[j]["low"]) <= current_low:
                is_swing = False
                break
        if not is_swing:
            continue

        surrounding = []
        for j in range(max(0, i - lookback * 2), min(n, i + lookback * 2 + 1)):
            if j == i:
                continue
            surrounding.append(float(candles[j]["low"]))
        avg_surrounding = _avg(surrounding, current_low)
        strength = (avg_surrounding - current_low) / max(avg_surrounding, 1e-9)

        local_high = max(float(candles[j]["high"]) for j in range(max(0, i - lookback), min(n, i + lookback + 1)))
        move_pct = ((local_high - current_low) / max(current_low, 1e-9)) * 100.0
        if move_pct < float(min_swing_pct):
            continue

        swings.append(
            {
                "index": i,
                "price": current_low,
                "time": candles[i].get("time"),
                "strength": round(min(max(strength * 100.0, 0.0), 1.0), 3),
                "move_pct": round(move_pct, 3),
            }
        )
    return swings


def _fib_determine_trend(swing_highs: List[Dict], swing_lows: List[Dict]) -> str:
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"
    hh = float(swing_highs[-1]["price"]) > float(swing_highs[-2]["price"])
    hl = float(swing_lows[-1]["price"]) > float(swing_lows[-2]["price"])
    lh = float(swing_highs[-1]["price"]) < float(swing_highs[-2]["price"])
    ll = float(swing_lows[-1]["price"]) < float(swing_lows[-2]["price"])
    if hh and hl:
        return "uptrend"
    if lh and ll:
        return "downtrend"
    return "ranging"


def _fib_calc_retracement(swing_high: float, swing_low: float, trend: str, pair: str) -> Dict:
    price_range = float(swing_high) - float(swing_low)
    if price_range <= 0:
        return {"levels": {}, "range": 0.0, "trend": trend}
    levels: Dict[float, float] = {}
    if trend == "uptrend":
        for ratio in FIBONACCI_RETRACEMENT_LEVELS.keys():
            levels[float(ratio)] = float(swing_high) - (price_range * float(ratio))
    else:
        for ratio in FIBONACCI_RETRACEMENT_LEVELS.keys():
            levels[float(ratio)] = float(swing_low) + (price_range * float(ratio))
    return {
        "levels": levels,
        "range": price_range,
        "range_pips": price_range / max(1e-9, _pip(pair)),
        "trend": trend,
        "swing_high": float(swing_high),
        "swing_low": float(swing_low),
    }


def _fib_calc_extension(swing_high: float, swing_low: float, retracement_point: float, trend: str) -> Dict:
    original_range = float(swing_high) - float(swing_low)
    if original_range <= 0:
        return {"levels": {}, "trend": trend}
    levels: Dict[float, float] = {}
    if trend == "uptrend":
        for ratio in FIBONACCI_EXTENSION_LEVELS.keys():
            levels[float(ratio)] = float(retracement_point) + (original_range * float(ratio))
    else:
        for ratio in FIBONACCI_EXTENSION_LEVELS.keys():
            levels[float(ratio)] = float(retracement_point) - (original_range * float(ratio))
    return {
        "levels": levels,
        "trend": trend,
        "swing_high": float(swing_high),
        "swing_low": float(swing_low),
        "retracement_point": float(retracement_point),
    }


def _fib_find_nearest_level(
    current_price: float,
    fib_levels: Dict[float, float],
    pair: str,
    tolerance_pips: float = 5.0,
) -> Optional[Dict]:
    if not fib_levels:
        return None
    tol = max(_pip(pair) * float(tolerance_pips), abs(float(current_price)) * 0.00005)
    best: Optional[Dict] = None
    for ratio, level_price in fib_levels.items():
        dist = abs(float(current_price) - float(level_price))
        if dist > tol:
            continue
        cand = {
            "ratio": float(ratio),
            "price": float(level_price),
            "distance_pips": round(dist / max(_pip(pair), 1e-9), 2),
            "percentage": f"{float(ratio) * 100.0:.1f}%",
            "is_key": float(ratio) in (0.382, 0.500, 0.618, 0.786),
        }
        if best is None or cand["distance_pips"] < best["distance_pips"]:
            best = cand
    return best


def _fib_is_psychological_level(pair: str, price: float) -> bool:
    pip = _pip(pair)
    # 50/100 pip rounds
    r50 = round(float(price) / (pip * 50.0)) * (pip * 50.0)
    r100 = round(float(price) / (pip * 100.0)) * (pip * 100.0)
    tol = pip * 5.0
    return abs(float(price) - r50) <= tol or abs(float(price) - r100) <= tol


def _fib_confluence_zones(
    pair: str,
    fib_levels: Dict[float, float],
    sr_supports: List[Dict],
    sr_resistances: List[Dict],
    demand_zones: List[Dict],
    supply_zones: List[Dict],
    ema_values: Dict[str, Optional[float]],
) -> List[Dict]:
    zones: List[Dict] = []
    if not fib_levels:
        return zones
    tol = max(_pip(pair) * 10.0, abs(_avg([v for v in fib_levels.values()], 0.0)) * 0.0001)

    for ratio, fib_price in fib_levels.items():
        if float(ratio) in (0.0, 1.0):
            continue
        factors = ["fibonacci"]
        desc: List[str] = []
        strength = 0.40

        for sr in (sr_supports or []) + (sr_resistances or []):
            lv = float(sr.get("level", 0.0))
            if abs(lv - float(fib_price)) <= tol:
                factors.append(f"sr_{str(sr.get('tf', 'tf')).lower()}")
                desc.append(f"{sr.get('tf', 'TF')} S/R")
                strength += 0.20
                break

        for z in demand_zones or []:
            if float(z.get("low", 0.0)) <= float(fib_price) <= float(z.get("high", 0.0)):
                factors.append("demand_zone")
                desc.append("Demand zone")
                strength += 0.25
                break
        for z in supply_zones or []:
            if float(z.get("low", 0.0)) <= float(fib_price) <= float(z.get("high", 0.0)):
                factors.append("supply_zone")
                desc.append("Supply zone")
                strength += 0.25
                break

        for ema_name, ema_val in (ema_values or {}).items():
            if ema_val is None:
                continue
            if abs(float(ema_val) - float(fib_price)) <= tol:
                factors.append(f"ema_{ema_name}")
                desc.append(f"EMA {ema_name}")
                strength += 0.15

        if _fib_is_psychological_level(pair, float(fib_price)):
            factors.append("psychological")
            desc.append("Psychological level")
            strength += 0.10

        if len(factors) >= 2:
            zones.append(
                {
                    "price": round(float(fib_price), 6),
                    "fib_level": float(ratio),
                    "fib_percentage": f"{float(ratio) * 100.0:.1f}%",
                    "factors": factors,
                    "description": desc,
                    "strength": round(min(strength, 1.0), 3),
                }
            )
    zones.sort(key=lambda z: (z["strength"], len(z.get("factors", []))), reverse=True)
    return zones


def _fib_generate_signals(
    pair: str,
    candles: List[Dict],
    trend: str,
    nearest_level: Optional[Dict],
) -> List[Dict]:
    signals: List[Dict] = []
    if not candles or len(candles) < 2 or not nearest_level:
        return signals
    ratio = float(nearest_level.get("ratio", -1))
    level_price = float(nearest_level.get("price", 0.0))
    c = candles[-1]
    low = float(c["low"])
    high = float(c["high"])
    close = float(c["close"])
    pip = _pip(pair)

    if ratio == 0.618:
        if trend == "uptrend" and low <= level_price + pip * 2 and close > level_price:
            signals.append(
                {
                    "type": "fib_golden_bounce",
                    "direction": "bullish",
                    "strength": 0.80,
                    "message": "Bullish bounce at Golden Ratio (61.8%).",
                    "level": nearest_level.get("percentage", "61.8%"),
                }
            )
        if trend == "downtrend" and high >= level_price - pip * 2 and close < level_price:
            signals.append(
                {
                    "type": "fib_golden_rejection",
                    "direction": "bearish",
                    "strength": 0.80,
                    "message": "Bearish rejection at Golden Ratio (61.8%).",
                    "level": nearest_level.get("percentage", "61.8%"),
                }
            )
    elif ratio == 0.500:
        if trend == "uptrend" and low <= level_price + pip * 2 and close > level_price:
            signals.append(
                {
                    "type": "fib_50_bounce",
                    "direction": "bullish",
                    "strength": 0.65,
                    "message": "Bullish bounce at 50% retracement.",
                    "level": nearest_level.get("percentage", "50.0%"),
                }
            )
        if trend == "downtrend" and high >= level_price - pip * 2 and close < level_price:
            signals.append(
                {
                    "type": "fib_50_rejection",
                    "direction": "bearish",
                    "strength": 0.65,
                    "message": "Bearish rejection at 50% retracement.",
                    "level": nearest_level.get("percentage", "50.0%"),
                }
            )
    elif ratio == 0.786:
        signals.append(
            {
                "type": "fib_deep_retracement",
                "direction": "neutral",
                "strength": 0.50,
                "message": "Deep retracement at 78.6% (reversal risk if broken).",
                "level": nearest_level.get("percentage", "78.6%"),
                "warning": True,
            }
        )
    elif ratio == 0.382:
        signals.append(
            {
                "type": "fib_shallow_retracement",
                "direction": "bullish" if trend == "uptrend" else ("bearish" if trend == "downtrend" else "neutral"),
                "strength": 0.55,
                "message": "Shallow retracement at 38.2% suggests strong trend continuation.",
                "level": nearest_level.get("percentage", "38.2%"),
            }
        )
    return signals


def _auto_fibonacci_analysis(
    candles: List[Dict],
    pair: str,
    timeframe: str,
    sr_supports: List[Dict],
    sr_resistances: List[Dict],
    demand_zones: List[Dict],
    supply_zones: List[Dict],
    ema_values: Dict[str, Optional[float]],
) -> Dict:
    out = {
        "timeframe": timeframe,
        "trend": "ranging",
        "ready": False,
        "swing_high": None,
        "swing_low": None,
        "retracement_levels": {},
        "extension_levels": {},
        "nearest_level": None,
        "signals": [],
        "confluence_zones": [],
        "high_probability_signals": [],
    }
    if not candles or len(candles) < 80:
        return out

    swings_h = _detect_swing_highs(candles, lookback=10, min_swing_pct=0.5)
    swings_l = _detect_swing_lows(candles, lookback=10, min_swing_pct=0.5)
    if not swings_h or not swings_l:
        return out

    trend = _fib_determine_trend(swings_h, swings_l)
    out["trend"] = trend
    current_price = float(candles[-1]["close"])

    swing_high: Optional[Dict] = None
    swing_low: Optional[Dict] = None
    if trend == "uptrend":
        low = swings_l[-1]
        highs_after = [h for h in swings_h if int(h["index"]) > int(low["index"])]
        if highs_after:
            swing_low = low
            swing_high = highs_after[-1]
    elif trend == "downtrend":
        high = swings_h[-1]
        lows_after = [l for l in swings_l if int(l["index"]) > int(high["index"])]
        if lows_after:
            swing_high = high
            swing_low = lows_after[-1]
    else:
        if swings_h[-1]["index"] > swings_l[-1]["index"]:
            swing_high = swings_h[-1]
            lows_before = [l for l in swings_l if int(l["index"]) < int(swing_high["index"])]
            swing_low = lows_before[-1] if lows_before else swings_l[-1]
            trend = "uptrend"
        else:
            swing_low = swings_l[-1]
            highs_before = [h for h in swings_h if int(h["index"]) < int(swing_low["index"])]
            swing_high = highs_before[-1] if highs_before else swings_h[-1]
            trend = "downtrend"

    if not swing_high or not swing_low:
        return out

    retr = _fib_calc_retracement(float(swing_high["price"]), float(swing_low["price"]), trend, pair)
    ext = _fib_calc_extension(float(swing_high["price"]), float(swing_low["price"]), current_price, trend)
    nearest = _fib_find_nearest_level(current_price, retr.get("levels", {}), pair, tolerance_pips=6.0)
    signals = _fib_generate_signals(pair, candles, trend, nearest)
    confluence_zones = _fib_confluence_zones(
        pair=pair,
        fib_levels=retr.get("levels", {}),
        sr_supports=sr_supports,
        sr_resistances=sr_resistances,
        demand_zones=demand_zones,
        supply_zones=supply_zones,
        ema_values=ema_values,
    )

    high_probability_signals: List[Dict] = []
    for z in confluence_zones:
        factors = z.get("factors", [])
        if float(z.get("strength", 0.0)) < 0.5 or len(factors) < 3:
            continue
        dist_pips = abs(float(z["price"]) - current_price) / max(_pip(pair), 1e-9)
        if dist_pips > 20.0:
            continue
        expected = "bullish" if current_price > float(z["price"]) else "bearish"
        trend_ok = (trend == "uptrend" and expected == "bullish") or (trend == "downtrend" and expected == "bearish")
        if not trend_ok:
            continue
        high_probability_signals.append(
            {
                "type": "fib_confluence",
                "direction": expected,
                "strength": z["strength"],
                "price": z["price"],
                "distance_pips": round(dist_pips, 2),
                "fib_level": z["fib_percentage"],
                "factors": factors,
                "message": f"HIGH PROBABILITY: {z['fib_percentage']} Fib + {len(factors)} confluences",
            }
        )

    out.update(
        {
            "trend": trend,
            "ready": True,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "retracement_levels": _fib_levels_to_dict(retr.get("levels", {})),
            "extension_levels": _fib_levels_to_dict(ext.get("levels", {})),
            "nearest_level": nearest,
            "signals": signals,
            "confluence_zones": confluence_zones[:8],
            "high_probability_signals": high_probability_signals[:4],
        }
    )
    return out


def _institutional_volume_context(candles: List[Dict], pair: str) -> Dict:
    out = {
        "price_trend": "flat",
        "volume_trend": "flat",
        "down_price_up_volume_bullish": False,
        "up_price_down_volume_bearish": False,
        "accumulation": False,
        "distribution": False,
        "green_triangle_bottom": False,
        "red_triangle_top": False,
        "partial_institutional_candle": False,
    }
    if len(candles) < 8:
        return out

    closes = [float(c.get("close", 0.0)) for c in candles]
    highs = [float(c.get("high", 0.0)) for c in candles]
    lows = [float(c.get("low", 0.0)) for c in candles]
    vols = [float(c.get("volume", 1.0) or 1.0) for c in candles]

    look = min(20, len(candles) - 1)
    price_delta = closes[-1] - closes[-look]
    px_thr = max(_pip(pair) * 4, abs(closes[-1]) * 0.00005)
    if price_delta > px_thr:
        out["price_trend"] = "up"
    elif price_delta < -px_thr:
        out["price_trend"] = "down"

    r_n = min(5, len(vols))
    p_n = min(max(3, look - r_n), len(vols) - r_n)
    recent_vol = _avg(vols[-r_n:], 1.0)
    prev_vol = _avg(vols[-(r_n + p_n):-r_n], 1.0)
    if recent_vol > prev_vol * 1.10:
        out["volume_trend"] = "up"
    elif recent_vol < prev_vol * 0.90:
        out["volume_trend"] = "down"

    out["down_price_up_volume_bullish"] = out["price_trend"] == "down" and out["volume_trend"] == "up"
    out["up_price_down_volume_bearish"] = out["price_trend"] == "up" and out["volume_trend"] == "down"

    zone_look = min(12, len(candles))
    zh = max(highs[-zone_look:])
    zl = min(lows[-zone_look:])
    zr = max(zh - zl, 1e-9)
    mclose = _avg(closes[-zone_look:], closes[-1])
    consolidation = (zr / max(abs(mclose), 1e-9)) <= 0.0035
    near_low = closes[-1] <= (zl + zr * 0.35)
    near_high = closes[-1] >= (zh - zr * 0.35)

    out["accumulation"] = consolidation and near_low and out["volume_trend"] == "up"
    out["distribution"] = consolidation and near_high and out["volume_trend"] == "up"
    out["green_triangle_bottom"] = bool(out["down_price_up_volume_bullish"] or out["accumulation"])
    out["red_triangle_top"] = bool(out["up_price_down_volume_bearish"] or out["distribution"])

    last = candles[-1]
    o = float(last.get("open", closes[-1]))
    h = float(last.get("high", closes[-1]))
    l = float(last.get("low", closes[-1]))
    c = float(last.get("close", closes[-1]))
    body = abs(c - o)
    rng = max(h - l, 1e-9)
    out["partial_institutional_candle"] = body <= rng * 0.35
    return out


def _arty_signal(
    candles: List[Dict],
    pair: str,
    sm21: Optional[float],
    sm50: Optional[float],
    sm100: Optional[float],
    sm200: Optional[float],
    sm200_prev: Optional[float],
    rsi14: Optional[float],
    adx14: Optional[float],
    atr14: float,
    pattern: Dict,
    institutional_volume_aligned: bool,
    zone_buy_context: bool,
    zone_sell_context: bool,
) -> Dict:
    out = {
        "buy_arrow_raw": False,
        "sell_arrow_raw": False,
        "buy_arrow": False,
        "sell_arrow": False,
        "cloud_state": "flat",
        "sm200_dynamic_state": "neutral",
        "touched_lines_buy": [],
        "touched_lines_sell": [],
        "buy_block_reasons": [],
        "sell_block_reasons": [],
        "engulfing_or_strike_boost_bull": False,
        "engulfing_or_strike_boost_bear": False,
        "choppy_market": False,
        "continuation_break_buy": False,
        "continuation_break_sell": False,
    }
    if len(candles) < 2 or sm21 is None or sm50 is None:
        return out

    c = candles[-1]
    p = candles[-2]
    o = float(c["open"])
    h = float(c["high"])
    l = float(c["low"])
    cl = float(c["close"])
    pc = float(p["close"])
    rng = max(h - l, 1e-9)
    body = abs(cl - o)
    body_ratio = body / rng
    close_strength_bull = cl >= (h - rng * 0.35)
    close_strength_bear = cl <= (l + rng * 0.35)

    if sm21 > sm50:
        out["cloud_state"] = "bullish"
    elif sm21 < sm50:
        out["cloud_state"] = "bearish"

    if sm200 is not None and sm200_prev is not None:
        if sm200 > sm200_prev and cl >= sm200:
            out["sm200_dynamic_state"] = "bullish"
        elif sm200 < sm200_prev and cl <= sm200:
            out["sm200_dynamic_state"] = "bearish"
        else:
            out["sm200_dynamic_state"] = "mixed"

    tol = max(float(atr14) * 0.10, _pip(pair) * 6)
    lines = [("sm21", sm21), ("sm50", sm50), ("sm100", sm100), ("sm200", sm200)]

    touched_buy = [name for name, lv in lines if lv is not None and l <= (float(lv) + tol) and cl > float(lv)]
    touched_sell = [name for name, lv in lines if lv is not None and h >= (float(lv) - tol) and cl < float(lv)]
    out["touched_lines_buy"] = touched_buy
    out["touched_lines_sell"] = touched_sell

    # User rule:
    # - bouncing ABOVE a line -> BUY
    # - bouncing BELOW a line -> SELL
    out["buy_arrow_raw"] = bool(touched_buy and cl > o and pc <= (sm21 + tol))
    out["sell_arrow_raw"] = bool(touched_sell and cl < o and pc >= (sm21 - tol))
    out["continuation_break_buy"] = bool(cl > float(p["high"]) + tol * 0.15)
    out["continuation_break_sell"] = bool(cl < float(p["low"]) - tol * 0.15)

    # Anti-chop filter: frequent candle direction flips inside compressed range.
    win = min(10, len(candles))
    d = []
    for x in candles[-win:]:
        xo = float(x["open"])
        xc = float(x["close"])
        d.append(1 if xc > xo else (-1 if xc < xo else 0))
    flips = 0
    for i in range(1, len(d)):
        if d[i] != 0 and d[i - 1] != 0 and d[i] != d[i - 1]:
            flips += 1
    hi_w = max(float(x["high"]) for x in candles[-win:])
    lo_w = min(float(x["low"]) for x in candles[-win:])
    compressed = (hi_w - lo_w) <= max(float(atr14) * 2.2, _pip(pair) * 24)
    choppy_market = flips >= max(4, win // 2) and compressed
    out["choppy_market"] = choppy_market

    names = set([str(pattern.get("name", ""))] + [str(x) for x in pattern.get("all_matches", [])])
    bull_boost = any(x in names for x in ("bullish_engulfing", "three_line_strike_bullish", "big_green_no_tiny_wick"))
    bear_boost = any(x in names for x in ("bearish_engulfing", "three_line_strike_bearish", "big_red_no_tiny_wick"))
    out["engulfing_or_strike_boost_bull"] = bull_boost
    out["engulfing_or_strike_boost_bear"] = bear_boost

    if out["buy_arrow_raw"]:
        reasons: List[str] = []
        touched_primary = any(x in touched_buy for x in ("sm21", "sm50")) or (zone_buy_context and "sm100" in touched_buy)
        if not touched_primary:
            reasons.append("Bounce is not on primary ARTY lines (21/50).")
        if out["cloud_state"] != "bullish":
            reasons.append("SMMA21 is not above SMMA50.")
        if sm100 is not None and sm50 < sm100:
            reasons.append("SMMA50 is below SMMA100.")
        if sm200 is not None and cl <= sm200:
            reasons.append("Price is not above SMMA200.")
        if out["sm200_dynamic_state"] == "bearish":
            reasons.append("SMMA200 dynamic control is bearish.")
        if rsi14 is not None and rsi14 < 50 and not bull_boost:
            reasons.append("RSI is below bullish confirmation zone.")
        min_adx = 18 if zone_buy_context else 22
        if adx14 is not None and adx14 < min_adx and not bull_boost:
            reasons.append("ADX too weak for reliable arrow.")
        if (not zone_buy_context) and (not out["continuation_break_buy"]) and (not bull_boost):
            reasons.append("No continuation break above previous high.")
        if cl <= (sm21 + tol * 0.20) and not bull_boost:
            reasons.append("Close not sufficiently above SMMA21 after bounce.")
        if not close_strength_bull and not bull_boost:
            reasons.append("Weak close near candle midpoint.")
        if body_ratio < 0.30 and not bull_boost:
            reasons.append("Arrow candle body is too weak.")
        if bool(pattern.get("indecision", False)):
            reasons.append("Indecision candle shape.")
        if choppy_market and not zone_buy_context and not bull_boost:
            reasons.append("Choppy market regime (high flip frequency).")
        if not institutional_volume_aligned and not bull_boost:
            reasons.append("Institutional volume confirmation missing.")
        out["buy_block_reasons"] = reasons
        out["buy_arrow"] = len(reasons) == 0

    if out["sell_arrow_raw"]:
        reasons = []
        touched_primary = any(x in touched_sell for x in ("sm21", "sm50")) or (zone_sell_context and "sm100" in touched_sell)
        if not touched_primary:
            reasons.append("Bounce is not on primary ARTY lines (21/50).")
        if out["cloud_state"] != "bearish":
            reasons.append("SMMA21 is not below SMMA50.")
        if sm100 is not None and sm50 > sm100:
            reasons.append("SMMA50 is above SMMA100.")
        if sm200 is not None and cl >= sm200:
            reasons.append("Price is not below SMMA200.")
        if out["sm200_dynamic_state"] == "bullish":
            reasons.append("SMMA200 dynamic control is bullish.")
        if rsi14 is not None and rsi14 > 50 and not bear_boost:
            reasons.append("RSI is above bearish confirmation zone.")
        min_adx = 18 if zone_sell_context else 22
        if adx14 is not None and adx14 < min_adx and not bear_boost:
            reasons.append("ADX too weak for reliable arrow.")
        if (not zone_sell_context) and (not out["continuation_break_sell"]) and (not bear_boost):
            reasons.append("No continuation break below previous low.")
        if cl >= (sm21 - tol * 0.20) and not bear_boost:
            reasons.append("Close not sufficiently below SMMA21 after bounce.")
        if not close_strength_bear and not bear_boost:
            reasons.append("Weak close near candle midpoint.")
        if body_ratio < 0.30 and not bear_boost:
            reasons.append("Arrow candle body is too weak.")
        if bool(pattern.get("indecision", False)):
            reasons.append("Indecision candle shape.")
        if choppy_market and not zone_sell_context and not bear_boost:
            reasons.append("Choppy market regime (high flip frequency).")
        if not institutional_volume_aligned and not bear_boost:
            reasons.append("Institutional volume confirmation missing.")
        out["sell_block_reasons"] = reasons
        out["sell_arrow"] = len(reasons) == 0

    return out


def _quality_rating(fired: float) -> str:
    # Max achievable fired = 11 primary signals + 1.0 candlestick bonus = 12.0
    if fired >= 11.5:   # All 11 primaries + strong bonus
        return "PLATINUM"
    if fired >= 9.0:    # Most primaries fired
        return "GOLD"
    if fired >= 7.0:    # Solid setup
        return "SILVER"
    if fired >= 5.0:    # Minimum viable confluence
        return "BRONZE"
    return "NO_TRADE"


def _nearest_with_priority(levels: List[Dict], price: float, tol: float) -> Optional[Dict]:
    # Prioritize higher-timeframe zones first when they are reasonably close.
    for tf in ("1W", "1D", "4H", "1H"):
        tf_levels = [z for z in levels if z.get("tf") == tf]
        if not tf_levels:
            continue
        candidate = _nearest(tf_levels, price)
        if candidate and abs(price - float(candidate["level"])) <= (tol * 1.8):
            return candidate
    return _nearest(levels, price)


def _count_level_retests(candles: List[Dict], level: float, tol: float, lookback_bars: int = 140) -> int:
    if not candles:
        return 0
    retests = 0
    for c in candles[-lookback_bars:]:
        if float(c["low"]) - tol <= level <= float(c["high"]) + tol:
            retests += 1
    return retests


def _candle_body_strength(c: Dict) -> float:
    body = abs(float(c["close"]) - float(c["open"]))
    span = max(float(c["high"]) - float(c["low"]), 1e-9)
    return body / span


def _zone_flip_confirmation(
    h1: List[Dict],
    level: Optional[float],
    direction: str,
    tol: float,
    lookback_bars: int = 100,
) -> Tuple[bool, float]:
    """
    Zone flip protocol:
    - bullish: break and close above resistance with a strong candle, then retest holds.
    - bearish: break and close below support with a strong candle, then retest rejects.
    Returns (confirmed, breakout_strength).
    """
    if not h1 or level is None or len(h1) < 12:
        return False, 0.0

    start = max(2, len(h1) - lookback_bars)
    lv = float(level)
    for i in range(start, len(h1)):
        c = h1[i]
        strength = _candle_body_strength(c)
        if strength < 0.55:
            continue

        close_i = float(c["close"])
        open_i = float(c["open"])

        if direction == "bullish":
            breakout = close_i > (lv + tol) and open_i <= (lv + tol)
            if not breakout:
                continue
            for j in range(i + 1, min(i + 9, len(h1))):
                r = h1[j]
                if float(r["low"]) <= (lv + tol) and float(r["close"]) >= (lv - tol * 0.25):
                    return True, strength
        else:
            breakout = close_i < (lv - tol) and open_i >= (lv - tol)
            if not breakout:
                continue
            for j in range(i + 1, min(i + 9, len(h1))):
                r = h1[j]
                if float(r["high"]) >= (lv - tol) and float(r["close"]) <= (lv + tol * 0.25):
                    return True, strength

    return False, 0.0


class SetupDetector:
    """Confluence detector: SCANNING / ALERT / SIGNAL."""

    CHECKS = [
        "fundamental_news_clear",
        "weekly_structure_aligned",
        "daily_structure_aligned",
        "h4_structure_aligned",
        "sr_zone_aligned",
        "supply_demand_zone_aligned",
        "rsi_level_direction_aligned",
        "rsi_divergence_aligned",
        "candlestick_pattern_aligned",
        "ema8_sma18_aligned",
        "ema200_side_aligned",
        "donchian_touch_aligned",
        "volume_institutional_aligned",
        "arty_signal_aligned",
        "macd_signal_aligned",
        "adx_strength_aligned",
        "parabolic_sar_aligned",
        "smc_signal_aligned",
        "fibonacci_confluence_aligned",
        "swing_ema_9_50_aligned",
        "session_timing_clear",
    ]
    OPTIONAL_CHECKS = {"candlestick_pattern_aligned"}
    CANDLE_BONUS_BASE = 0.33
    CANDLE_BONUS_ZONE_MULT = 3.0
    CANDLE_BONUS_MAX = 1.0

    MISSING_HINTS = {
        "fundamental_news_clear": "News filter must be clear",
        "weekly_structure_aligned": "Weekly structure alignment missing",
        "daily_structure_aligned": "Daily structure alignment missing",
        "h4_structure_aligned": "H4 structure alignment missing",
        "sr_zone_aligned": "S/R zone not valid yet (need clean touch or zone-flip retest hold)",
        "supply_demand_zone_aligned": "Supply/Demand not qualified (H4 priority, EMA8/SMA18, volume, wick/body checks)",
        "rsi_level_direction_aligned": "RSI 75/25 setup not confirmed with zone/candle rules",
        "rsi_divergence_aligned": "RSI divergence missing or lacks supporting context",
        "candlestick_pattern_aligned": "Candlestick confirmation missing",
        "ema8_sma18_aligned": "EMA8/SMA18 trend direction mismatch",
        "ema200_side_aligned": "EMA200 side alignment missing",
        "donchian_touch_aligned": "Donchian touch missing or lacking confirmation (RSI/divergence/momentum/zone)",
        "volume_institutional_aligned": "Institutional volume context missing (accumulation/distribution or volume-fight alignment)",
        "arty_signal_aligned": "ARTY arrow missing or filtered as fake by trend/RSI/macro checks",
        "macd_signal_aligned": "MACD/OsMA momentum state not aligned",
        "adx_strength_aligned": "ADX>25 with DI directional dominance not confirmed",
        "parabolic_sar_aligned": "Parabolic SAR streak missing",
        "smc_signal_aligned": "SMC trigger missing (OB/BOS/CHOCH/FVG/liquidity/premium-discount)",
        "fibonacci_confluence_aligned": "Fibonacci retracement/confluence not aligned (50/61.8 + zone/EMA/SR)",
        "swing_ema_9_50_aligned": "Swing module missing (EMA 9/50/90 + SAR 3+ + RSI 50 zone + EMA200 side)",
        "session_timing_clear": "Session timing not in London/NY windows",
    }

    def __init__(self, pair: str, refresh_sec: int = 180):
        self.pair = pair
        self.refresh_sec = max(60, int(refresh_sec))
        self._ctx_ts = 0.0
        self._ctx: Dict = {}
        self._fetcher = OandaCandleFetcher(
            account_id=OANDA_ACCOUNT_ID,
            api_key=OANDA_API_KEY,
            practice=(str(OANDA_ENV).lower() != "live"),
        )

    def _tf(self, gran: str, count: int = 260) -> List[Dict]:
        try:
            return self._fetcher.get_candles(self.pair, gran, count=count) or []
        except Exception:
            return []

    def _refresh(self) -> None:
        h1 = self._tf("H1", 500)
        if not h1:
            _now = datetime.now(timezone.utc).timestamp()
            if _now - getattr(self, "_last_h1_warn_ts", 0) >= 60:
                print(f"[WARN] SetupDetector ({self.pair}): H1 candles unavailable — context not updated, will retry next cycle.")
                self._last_h1_warn_ts = _now
            return
        h4 = self._tf("H4", 360)
        d1 = self._tf("D", 320)
        w1 = self._tf("W", 260)

        def sr(candles: List[Dict], tf: str) -> Tuple[List[Dict], List[Dict]]:
            if len(candles) < 30:
                return [], []
            # SRv2-style defaults: pivot period=10, max pivots=20, channel width=10%.
            max_age = {
                "1W": 180,   # keep recent relevant weekly zones
                "1D": 220,   # ignore too-old daily levels
                "4H": 280,
                "1H": 220,
            }.get(tf, 240)
            highs, lows = _find_pivots(
                candles,
                lb=10,
                max_pivots=20,
                max_age_bars=max_age,
            )
            pr = max(float(c["high"]) for c in candles) - min(float(c["low"]) for c in candles)
            width = max(pr * 0.10, 1e-9)
            return _cluster(lows, width), _cluster(highs, width)

        sw, rw = sr(w1, "1W")
        sd, rd = sr(d1, "1D")
        s4, r4 = sr(h4, "4H")
        s1, r1 = sr(h1, "1H")
        supply_h4, demand_h4 = _detect_supply_demand_zones(h4, self.pair, tf_label="4H")
        supply_h1, demand_h1 = _detect_supply_demand_zones(h1, self.pair, tf_label="1H")
        supply_zones = sorted(supply_h4 + supply_h1, key=lambda z: (z.get("tf") != "4H", -float(z.get("score", 0.0))))[:10]
        demand_zones = sorted(demand_h4 + demand_h1, key=lambda z: (z.get("tf") != "4H", -float(z.get("score", 0.0))))[:10]
        last = float(h1[-1]["close"]) if h1 else 0.0
        tol = max(_pip(self.pair) * 8, abs(last) * 0.0003)
        near = _nearest_with_priority(s1 + r1, last, tol)
        retests = _count_level_retests(h1, float(near["level"]), tol) if near else 0

        for item in sw:
            item["tf"] = "1W"
        for item in rw:
            item["tf"] = "1W"
        for item in sd:
            item["tf"] = "1D"
        for item in rd:
            item["tf"] = "1D"
        for item in s4:
            item["tf"] = "4H"
        for item in r4:
            item["tf"] = "4H"
        for item in s1:
            item["tf"] = "1H"
        for item in r1:
            item["tf"] = "1H"

        self._ctx = {
            "h1": h1,
            "h4": h4,
            "d1": d1,
            "w1": w1,
            "supports": sw + sd + s4 + s1,
            "resistances": rw + rd + r4 + r1,
            "supply_zones": supply_zones,
            "demand_zones": demand_zones,
            "h1_retests": retests,
            "fakeout_risk": retests >= 4,
            "h1_warning_retests": retests >= 3,
            "weekly_trend": _tf_trend(w1, 50),
            "daily_trend": _tf_trend(d1, 50),
            "h4_trend": _tf_trend(h4, 50),
        }
        self._ctx_ts = datetime.now(timezone.utc).timestamp()

    def analyze(
        self,
        candle: Dict,
        history: List[Dict],
        structure: Dict,
        liquidity: Dict,
        trend: str,
        volume_ok: bool,
        mtf_bias: str,
        session_ok: bool,
        news_clear: bool,
        news_impact: str = "none",
        volume_context: Optional[Dict] = None,
    ) -> Dict:
        now = datetime.now(timezone.utc).timestamp()
        if not self._ctx or now - self._ctx_ts >= self.refresh_sec:
            self._refresh()

        candles = (history or []) + [candle]
        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        price = closes[-1]
        e8 = ema(closes, 8)
        s18 = sma(closes, 18)
        e9 = ema(closes, 9)
        e50 = ema(closes, 50)
        e90 = ema(closes, 90)
        e200 = ema(closes, 200)
        sm21 = smma(closes, 21)
        sm50 = smma(closes, 50)
        sm100 = smma(closes, 100)
        sm200 = smma(closes, 200)
        sm200_prev = smma(closes[:-1], 200) if len(closes) >= 201 else None
        rsi_vals = rsi_series(closes, RSI_LENGTH)
        rsi14 = rsi_vals[-1] if rsi_vals else None
        macd_pack = macd_components(closes)
        macd_line = macd_pack.get("line")
        macd_signal = macd_pack.get("signal")
        mch = macd_pack.get("hist")
        mch_prev = macd_pack.get("hist_prev")
        osma = macd_pack.get("osma")
        osma_prev = macd_pack.get("osma_prev")
        macd_hist_switch_positive = bool(macd_pack.get("hist_switch_positive"))
        macd_hist_switch_negative = bool(macd_pack.get("hist_switch_negative"))
        macd_zero_cross_above = bool(macd_pack.get("line_cross_above_zero"))
        macd_zero_cross_below = bool(macd_pack.get("line_cross_below_zero"))
        osma_rising = bool(macd_pack.get("osma_rising"))
        osma_falling = bool(macd_pack.get("osma_falling"))
        adx14, pdi, mdi = adx(candles, 14)
        up, low = donchian(candles, 20)
        donchian_basis = ((float(up) + float(low)) / 2.0) if (up is not None and low is not None) else None
        a14 = atr(candles, 14) or (_pip(self.pair) * 20)
        sar_side, sar_val, sar_streak = parabolic_sar_signal(
            candles,
            start=SAR_START,
            increment=SAR_INCREMENT,
            maximum=SAR_MAXIMUM,
        )
        # P10 fix: compute RSI divergence on H1 candles (not noisy M1 ticks).
        _h1_for_div = self._ctx.get("h1", []) or candles
        _h1_closes_div = [float(c["close"]) for c in _h1_for_div]
        _rsi_vals_h1 = rsi_series(_h1_closes_div, RSI_LENGTH) if len(_h1_closes_div) > RSI_LENGTH else [None] * len(_h1_closes_div)
        divergence = _find_divergence(_h1_for_div, _rsi_vals_h1)
        pattern = _candlestick_signal(candles)
        fvg = _detect_fvg(candles)
        macro_bias_ema200 = "neutral"
        if e200 is not None:
            if price > e200:
                macro_bias_ema200 = "bullish"
            elif price < e200:
                macro_bias_ema200 = "bearish"

        # MODULE 9: Swing strategy confirmations.
        rsi_swing_buy_zone = bool(rsi14 is not None and rsi14 > RSI_SWING_MID)
        rsi_swing_sell_zone = bool(rsi14 is not None and rsi14 < RSI_SWING_MID)
        ema9_above_ema50 = bool(e9 is not None and e50 is not None and e9 > e50)
        ema50_above_ema9 = bool(e9 is not None and e50 is not None and e50 > e9)
        # EMA90 acts as slow trend filter for swing quality.
        ema50_above_ema90 = bool(e50 is not None and e90 is not None and e50 > e90)
        ema50_below_ema90 = bool(e50 is not None and e90 is not None and e50 < e90)
        sar_buy_ready = bool(sar_side == "bullish" and sar_streak >= 3)
        sar_sell_ready = bool(sar_side == "bearish" and sar_streak >= 3)
        ema200_buy_side = bool(e200 is not None and price > e200)
        ema200_sell_side = bool(e200 is not None and price < e200)
        swing_buy_ready = bool(
            ema9_above_ema50
            and ema50_above_ema90
            and sar_buy_ready
            and rsi_swing_buy_zone
            and ema200_buy_side
        )
        swing_sell_ready = bool(
            ema50_above_ema9
            and ema50_below_ema90
            and sar_sell_ready
            and rsi_swing_sell_zone
            and ema200_sell_side
        )

        bias = mtf_bias if mtf_bias in ("bullish", "bearish") else trend
        if bias not in ("bullish", "bearish"):
            wk = self._ctx.get("weekly_trend", "neutral")
            d1_trend = self._ctx.get("daily_trend", "neutral")
            if wk == d1_trend and wk in ("bullish", "bearish"):
                bias = wk
            elif e8 and s18 and e8 > s18:
                bias = "bullish"
            elif e8 and s18 and e8 < s18:
                bias = "bearish"
            elif macro_bias_ema200 in ("bullish", "bearish"):
                bias = macro_bias_ema200
            else:
                bias = "neutral"

        supports_all = self._ctx.get("supports", [])
        resistances_all = self._ctx.get("resistances", [])
        tol = max(a14 * 0.20, _pip(self.pair) * 8, abs(price) * 0.0001)
        ns = _nearest_with_priority(supports_all, price, tol)
        nr = _nearest_with_priority(resistances_all, price, tol)
        sr_buy = bool(ns and abs(price - float(ns["level"])) <= tol)
        sr_sell = bool(nr and abs(price - float(nr["level"])) <= tol)

        demand_zones = self._ctx.get("demand_zones", [])
        supply_zones = self._ctx.get("supply_zones", [])
        nearest_demand = _nearest_zone_with_priority(demand_zones, price)
        nearest_supply = _nearest_zone_with_priority(supply_zones, price)
        in_demand = _zone_hit(price, nearest_demand, tol)
        in_supply = _zone_hit(price, nearest_supply, tol)

        demand_rejection_wick = _zone_wick_rejection(candle, nearest_demand, "bullish", tol) if in_demand else False
        supply_rejection_wick = _zone_wick_rejection(candle, nearest_supply, "bearish", tol) if in_supply else False

        ema_sma_up = bool(e8 is not None and s18 is not None and e8 > s18)
        ema_sma_down = bool(e8 is not None and s18 is not None and e8 < s18)

        # Module 2 confirmation:
        # EMA below SMA => downtrend (supply sell continuation / demand break risk)
        # SMA below EMA => uptrend (demand buy continuation / supply break risk)
        demand_trend_confirm = in_demand and ema_sma_up
        supply_trend_confirm = in_supply and ema_sma_down
        zone_trend_mismatch = (in_demand and ema_sma_down) or (in_supply and ema_sma_up)

        h4_zone_priority_ok = bool(
            (bias == "bullish" and nearest_demand and str(nearest_demand.get("tf", "")) == "4H")
            or (bias == "bearish" and nearest_supply and str(nearest_supply.get("tf", "")) == "4H")
        )
        h4_demand_near = any(str(z.get("tf", "")) == "4H" and _zone_hit(price, z, tol * 1.5) for z in demand_zones)
        h4_supply_near = any(str(z.get("tf", "")) == "4H" and _zone_hit(price, z, tol * 1.5) for z in supply_zones)
        zone_priority_pass = bool(
            (bias == "bullish" and (h4_zone_priority_ok or not h4_demand_near))
            or (bias == "bearish" and (h4_zone_priority_ok or not h4_supply_near))
        )

        # Use H1 candles from the OANDA candle API for volume analysis.
        # H1 candles carry real traded volume; M1 tick-built candles only count
        # tick frequency which reflects spread/volatility, not institutional flow.
        _h1_vol_candles = self._ctx.get("h1", []) or candles
        volume_ctx = _institutional_volume_context(_h1_vol_candles, self.pair)
        inst_volume_bull = bool(
            volume_ctx.get("down_price_up_volume_bullish")
            or volume_ctx.get("accumulation")
            or volume_ctx.get("green_triangle_bottom")
        )
        inst_volume_bear = bool(
            volume_ctx.get("up_price_down_volume_bearish")
            or volume_ctx.get("distribution")
            or volume_ctx.get("red_triangle_top")
        )
        institutional_volume_aligned = bool(
            volume_ok
            or (bias == "bullish" and inst_volume_bull)
            or (bias == "bearish" and inst_volume_bear)
        )

        zone_volume_confirm = bool(
            institutional_volume_aligned
            and (
                (bias == "bullish" and demand_trend_confirm)
                or (bias == "bearish" and supply_trend_confirm)
            )
        )

        buy_side_stop_hunt = bool(liquidity.get("buy_side_stop_hunt"))
        sell_side_stop_hunt = bool(liquidity.get("sell_side_stop_hunt"))
        aligned_liquidity_grab = bool(
            (bias == "bullish" and sell_side_stop_hunt and (sr_buy or in_demand))
            or (bias == "bearish" and buy_side_stop_hunt and (sr_sell or in_supply))
        )
        opposing_liquidity_grab = bool(
            (bias == "bullish" and buy_side_stop_hunt)
            or (bias == "bearish" and sell_side_stop_hunt)
        )

        at_key_zone = bool(sr_buy or sr_sell or in_demand or in_supply)
        candlestick_direction_aligned = bool(
            (bias == "bullish" and pattern["bullish"]) or (bias == "bearish" and pattern["bearish"])
        )
        candlestick_confirmed = bool(
            candlestick_direction_aligned
            and not bool(pattern.get("requires_confirmation", False))
            and not bool(pattern.get("indecision", False))
        )
        # Candlestick is an optional confirmation only.
        # It can boost confidence, but cannot carry the setup by itself.
        candlestick_bonus = 0.0
        if candlestick_direction_aligned:
            candlestick_bonus = self.CANDLE_BONUS_BASE
            if at_key_zone:
                # Keep the user-requested 3x relative effect near key zones,
                # but cap absolute influence to 1.0 point overall.
                candlestick_bonus *= self.CANDLE_BONUS_ZONE_MULT
            if bool(pattern.get("requires_confirmation", False)):
                candlestick_bonus *= 0.5
            if bool(pattern.get("indecision", False)):
                candlestick_bonus = 0.0
            candlestick_bonus = min(self.CANDLE_BONUS_MAX, candlestick_bonus)

        # MODULE 3: RSI + Divergence protocol (very strong, but never blind).
        rsi_overbought = bool(rsi14 is not None and rsi14 >= RSI_UPPER_BAND)
        rsi_oversold = bool(rsi14 is not None and rsi14 <= RSI_LOWER_BAND)
        at_resistance_context = bool(sr_sell or in_supply)
        at_support_context = bool(sr_buy or in_demand)
        bearish_div = divergence == "bearish"
        bullish_div = divergence == "bullish"
        bearish_candle_confirm = bool(pattern["bearish"])
        bullish_candle_confirm = bool(pattern["bullish"])

        # If RSI extreme at zone: require candlestick confirmation for trade-ready.
        # If RSI extreme away from zone: divergence OR candlestick can qualify.
        rsi_sell_ready = bool(
            rsi_overbought
            and (
                (at_resistance_context and bearish_candle_confirm)
                or ((not at_resistance_context) and (bearish_div or bearish_candle_confirm))
            )
        )
        rsi_buy_ready = bool(
            rsi_oversold
            and (
                (at_support_context and bullish_candle_confirm)
                or ((not at_support_context) and (bullish_div or bullish_candle_confirm))
            )
        )

        divergence_context_ok = bool(
            (bearish_div and ((at_resistance_context and bias == "bearish") or macro_bias_ema200 == "bearish"))
            or (bullish_div and ((at_support_context and bias == "bullish") or macro_bias_ema200 == "bullish"))
        )

        flip_buy_ok, flip_buy_strength = _zone_flip_confirmation(
            self._ctx.get("h1", []),
            float(nr["level"]) if nr else None,
            "bullish",
            tol,
        )
        flip_sell_ok, flip_sell_strength = _zone_flip_confirmation(
            self._ctx.get("h1", []),
            float(ns["level"]) if ns else None,
            "bearish",
            tol,
        )

        sr_buy = sr_buy or flip_buy_ok
        sr_sell = sr_sell or flip_sell_ok

        # Daily-zone rejection handling: require at least 2 touches to avoid intermediate noise.
        daily_rejection_caution = False
        if ns and ns.get("tf") == "1D" and int(ns.get("touches", 0)) < 2 and bias == "bullish":
            daily_rejection_caution = True
        if nr and nr.get("tf") == "1D" and int(nr.get("touches", 0)) < 2 and bias == "bearish":
            daily_rejection_caution = True

        arty = _arty_signal(
            candles=candles,
            pair=self.pair,
            sm21=sm21,
            sm50=sm50,
            sm100=sm100,
            sm200=sm200,
            sm200_prev=sm200_prev,
            rsi14=rsi14,
            adx14=adx14,
            atr14=a14,
            pattern=pattern,
            institutional_volume_aligned=institutional_volume_aligned,
            zone_buy_context=bool(sr_buy or in_demand),
            zone_sell_context=bool(sr_sell or in_supply),
        )
        arty_buy = bool(arty.get("buy_arrow"))
        arty_sell = bool(arty.get("sell_arrow"))

        # MODULE 7: Donchian(20) boundary logic + mandatory confirmation.
        donchian_buy_touch = bool(low is not None and price <= float(low) + tol)
        donchian_sell_touch = bool(up is not None and price >= float(up) - tol)
        donchian_rsi_max_confluence = bool(
            (donchian_buy_touch and rsi_oversold)
            or (donchian_sell_touch and rsi_overbought)
        )
        donchian_secondary_bull = bool(
            rsi_oversold
            or bullish_div
            or candlestick_direction_aligned
            or (mch is not None and mch > 0)
            or ema_sma_up
            or institutional_volume_aligned
        )
        donchian_secondary_bear = bool(
            rsi_overbought
            or bearish_div
            or candlestick_direction_aligned
            or (mch is not None and mch < 0)
            or ema_sma_down
            or institutional_volume_aligned
        )
        donchian_touch_aligned = bool(
            (bias == "bullish" and donchian_buy_touch and donchian_secondary_bull)
            or (bias == "bearish" and donchian_sell_touch and donchian_secondary_bear)
        )

        # MODULE 8: MACD + OsMA + ADX rules.
        macd_bull_align = bool(
            (mch is not None and mch > 0)
            and (macd_hist_switch_positive or osma_rising or macd_zero_cross_above)
        )
        macd_bear_align = bool(
            (mch is not None and mch < 0)
            and (macd_hist_switch_negative or osma_falling or macd_zero_cross_below)
        )
        adx_trending = bool(adx14 is not None and adx14 > 25)
        adx_ranging = bool(adx14 is not None and adx14 < 20)
        adx_directional_bull = bool(adx_trending and pdi is not None and mdi is not None and pdi > mdi)
        adx_directional_bear = bool(adx_trending and pdi is not None and mdi is not None and mdi > pdi)
        smc_ctx = _smc_context(
            candles=candles,
            pair=self.pair,
            bias=bias,
            structure=structure,
            liquidity=liquidity,
            fvg=fvg,
            in_demand=in_demand,
            in_supply=in_supply,
            tol=tol,
        )
        chart_patterns = _detect_chart_patterns(candles, self.pair)
        chart_pattern_direction = str(chart_patterns.get("direction", "neutral"))
        chart_pattern_aligned = bool(
            (bias == "bullish" and chart_pattern_direction == "bullish")
            or (bias == "bearish" and chart_pattern_direction == "bearish")
        )
        h4_candles = self._ctx.get("h4", []) or []
        h4_closes = [float(c["close"]) for c in h4_candles]
        h4_e50 = ema(h4_closes, 50) if h4_closes else None
        h4_e200 = ema(h4_closes, 200) if h4_closes else None
        fib_h1 = _auto_fibonacci_analysis(
            candles=self._ctx.get("h1", []) or candles,
            pair=self.pair,
            timeframe="H1",
            sr_supports=supports_all,
            sr_resistances=resistances_all,
            demand_zones=demand_zones,
            supply_zones=supply_zones,
            ema_values={"50": e50, "200": e200},
        )
        fib_h4 = _auto_fibonacci_analysis(
            candles=h4_candles,
            pair=self.pair,
            timeframe="4H",
            sr_supports=[z for z in supports_all if str(z.get("tf", "")).upper() in ("4H", "1D", "1W")],
            sr_resistances=[z for z in resistances_all if str(z.get("tf", "")).upper() in ("4H", "1D", "1W")],
            demand_zones=[z for z in demand_zones if str(z.get("tf", "")).upper() == "4H"],
            supply_zones=[z for z in supply_zones if str(z.get("tf", "")).upper() == "4H"],
            ema_values={"50": h4_e50, "200": h4_e200},
        )

        def _fib_rank(pack: Dict) -> float:
            if not pack or not pack.get("ready"):
                return -1.0
            score = 0.0
            if pack.get("nearest_level"):
                score += 1.0
            score += float(len(pack.get("signals", []) or [])) * 0.6
            score += float(len(pack.get("high_probability_signals", []) or [])) * 1.2
            conf = pack.get("confluence_zones", []) or []
            score += float(conf[0].get("strength", 0.0)) if conf else 0.0
            return score

        fib_candidates = [p for p in [fib_h1, fib_h4] if p and p.get("ready")]
        fib_active = sorted(fib_candidates, key=_fib_rank, reverse=True)[0] if fib_candidates else None
        fib_nearest = fib_active.get("nearest_level") if fib_active else None
        fib_active_trend = str(fib_active.get("trend", "ranging")) if fib_active else "ranging"
        fib_high_prob = fib_active.get("high_probability_signals", []) if fib_active else []
        fib_confluence_top = (fib_active.get("confluence_zones") or [None])[0] if fib_active else None

        fib_zone_overlap = False
        if fib_nearest:
            fib_price = float(fib_nearest.get("price", price))
            fib_zone_overlap = bool(
                (ns and abs(fib_price - float(ns["level"])) <= tol * 1.2)
                or (nr and abs(fib_price - float(nr["level"])) <= tol * 1.2)
                or (nearest_demand and (float(nearest_demand["low"]) - tol) <= fib_price <= (float(nearest_demand["high"]) + tol))
                or (nearest_supply and (float(nearest_supply["low"]) - tol) <= fib_price <= (float(nearest_supply["high"]) + tol))
            )

        fib_bias_alignment = bool(
            (bias == "bullish" and fib_active_trend == "uptrend")
            or (bias == "bearish" and fib_active_trend == "downtrend")
        )
        fib_high_prob_alignment = any(
            (bias == "bullish" and str(s.get("direction")) == "bullish")
            or (bias == "bearish" and str(s.get("direction")) == "bearish")
            for s in fib_high_prob
        )
        fib_nearest_key = bool(fib_nearest and fib_nearest.get("is_key"))
        fib_confluence_aligned = bool(
            fib_bias_alignment
            and (
                fib_high_prob_alignment
                or (
                    fib_nearest_key
                    and fib_zone_overlap
                    and ((bias == "bullish" and (sr_buy or in_demand)) or (bias == "bearish" and (sr_sell or in_supply)))
                )
            )
        )

        checklist = {
            "fundamental_news_clear": bool(news_clear),
            "weekly_structure_aligned": self._ctx.get("weekly_trend") == bias and bias in ("bullish", "bearish"),
            "daily_structure_aligned": self._ctx.get("daily_trend") == bias and bias in ("bullish", "bearish"),
            "h4_structure_aligned": self._ctx.get("h4_trend") == bias and bias in ("bullish", "bearish"),
            "sr_zone_aligned": ((bias == "bullish" and sr_buy) or (bias == "bearish" and sr_sell)) and not daily_rejection_caution,
            "supply_demand_zone_aligned": zone_volume_confirm and zone_priority_pass and not zone_trend_mismatch,
            "rsi_level_direction_aligned": (bias == "bullish" and rsi_buy_ready) or (bias == "bearish" and rsi_sell_ready),
            "rsi_divergence_aligned": divergence_context_ok,
            "candlestick_pattern_aligned": candlestick_confirmed,
            "ema8_sma18_aligned": (bias == "bullish" and ema_sma_up) or (bias == "bearish" and ema_sma_down),
            "ema200_side_aligned": (bias == "bullish" and e200 and price > e200) or (bias == "bearish" and e200 and price < e200),
            "donchian_touch_aligned": donchian_touch_aligned,
            "volume_institutional_aligned": institutional_volume_aligned,
            "arty_signal_aligned": (bias == "bullish" and arty_buy) or (bias == "bearish" and arty_sell),
            "macd_signal_aligned": (bias == "bullish" and macd_bull_align) or (bias == "bearish" and macd_bear_align),
            "adx_strength_aligned": (bias == "bullish" and adx_directional_bull) or (bias == "bearish" and adx_directional_bear),
            "parabolic_sar_aligned": (bias == "bullish" and sar_buy_ready) or (bias == "bearish" and sar_sell_ready),
            "smc_signal_aligned": bool(smc_ctx.get("smc_signal_aligned")),
            "fibonacci_confluence_aligned": fib_confluence_aligned,
            # Module 11 stays confirmation-only (not included in required confluence total).
            "chart_pattern_confirmation": chart_pattern_aligned,
            "swing_ema_9_50_aligned": (bias == "bullish" and swing_buy_ready) or (bias == "bearish" and swing_sell_ready),
            "session_timing_clear": bool(session_ok),
        }
        extreme_range_setup = bool(
            adx_ranging
            and (
                (bias == "bullish" and donchian_buy_touch and rsi_oversold and (sr_buy or in_demand))
                or (bias == "bearish" and donchian_sell_touch and rsi_overbought and (sr_sell or in_supply))
            )
        )

        # TRADE ENTRY DECISION ENGINE (explicit tier logic from strategy rules).
        decision_signals = {
            "zone": bool(checklist.get("sr_zone_aligned") or checklist.get("supply_demand_zone_aligned")),
            "rsi_divergence": bool(checklist.get("rsi_level_direction_aligned") and checklist.get("rsi_divergence_aligned")),
            "candlestick": bool(checklist.get("candlestick_pattern_aligned")),
            "ema_sma": bool(checklist.get("ema8_sma18_aligned")),
            "donchian": bool(checklist.get("donchian_touch_aligned")),
            "volume": bool(checklist.get("volume_institutional_aligned")),
            "macd_adx": bool(checklist.get("macd_signal_aligned") and checklist.get("adx_strength_aligned")),
            "smc": bool(checklist.get("smc_signal_aligned")),
            "parabolic_sar": bool(checklist.get("parabolic_sar_aligned")),
            "fibonacci": bool(checklist.get("fibonacci_confluence_aligned")),
            "arty": bool(checklist.get("arty_signal_aligned")),
        }
        primary_confluence_count = sum(1 for v in decision_signals.values() if v)
        primary_confluence_total = len(decision_signals)
        tier_a_candidate = primary_confluence_count >= 4
        tier_b_candidate = (
            2 <= primary_confluence_count <= 3
            and bool(decision_signals["rsi_divergence"] or decision_signals["zone"])
        )
        tier_c_condition = primary_confluence_count <= 1

        # Scaled quality keeps existing badge ranges while using the new 11-signal engine.
        quality = _quality_rating((primary_confluence_count / max(primary_confluence_total, 1)) * 20.0)

        required_checks = [k for k in self.CHECKS if k not in self.OPTIONAL_CHECKS]
        fired_required = sum(1 for k in required_checks if checklist.get(k))
        fired = float(primary_confluence_count)
        total = float(len(required_checks)) + float(self.CANDLE_BONUS_MAX)
        confluence_total_primary = float(primary_confluence_total)

        sl_hardening_notes: List[str] = []
        if bias == "bullish":
            candidates = [float(candle["low"]) - max(a14 * 0.25, _pip(self.pair) * 5)]
            if ns:
                candidates.append(float(ns["level"]) - max(a14 * 0.25, _pip(self.pair) * 5))
            if nearest_demand:
                candidates.append(float(nearest_demand["low"]) - max(a14 * 0.25, _pip(self.pair) * 5))
            sl = min(candidates)
            sl, sl_hardening_notes = _harden_stop_loss(
                sl=sl,
                pair=self.pair,
                direction="BUY",
                atr_val=a14,
                sr_level=float(ns["level"]) if ns else None,
                zone_low=float(nearest_demand["low"]) if nearest_demand else None,
                zone_high=float(nearest_demand["high"]) if nearest_demand else None,
                flip_level=float(nr["level"]) if flip_buy_ok and nr else None,
            )
            risk = max(price - sl, _pip(self.pair) * 5)
            tp1, tp2, tp3 = price + risk, price + 2 * risk, price + 3 * risk
        elif bias == "bearish":
            candidates = [float(candle["high"]) + max(a14 * 0.25, _pip(self.pair) * 5)]
            if nr:
                candidates.append(float(nr["level"]) + max(a14 * 0.25, _pip(self.pair) * 5))
            if nearest_supply:
                candidates.append(float(nearest_supply["high"]) + max(a14 * 0.25, _pip(self.pair) * 5))
            sl = max(candidates)
            sl, sl_hardening_notes = _harden_stop_loss(
                sl=sl,
                pair=self.pair,
                direction="SELL",
                atr_val=a14,
                sr_level=float(nr["level"]) if nr else None,
                zone_low=float(nearest_supply["low"]) if nearest_supply else None,
                zone_high=float(nearest_supply["high"]) if nearest_supply else None,
                flip_level=float(ns["level"]) if flip_sell_ok and ns else None,
            )
            risk = max(sl - price, _pip(self.pair) * 5)
            tp1, tp2, tp3 = price - risk, price - 2 * risk, price - 3 * risk
        else:
            sl = price
            risk = 0.0
            tp1 = tp2 = tp3 = price

        fib_tp_override = False
        fib_retr_levels = fib_active.get("retracement_levels", {}) if fib_active else {}
        fib_ext_levels = fib_active.get("extension_levels", {}) if fib_active else {}
        fib_swing_high = float((fib_active.get("swing_high") or {}).get("price", 0.0)) if fib_active and fib_active.get("swing_high") else None
        fib_swing_low = float((fib_active.get("swing_low") or {}).get("price", 0.0)) if fib_active and fib_active.get("swing_low") else None
        fib_ext_127 = float(fib_ext_levels.get("1.272")) if fib_ext_levels and fib_ext_levels.get("1.272") is not None else None
        fib_ext_161 = float(fib_ext_levels.get("1.618")) if fib_ext_levels and fib_ext_levels.get("1.618") is not None else None

        if risk > 0 and fib_active and fib_bias_alignment:
            if bias == "bullish":
                if fib_swing_high is not None and fib_swing_high > price:
                    tp1 = max(tp1, fib_swing_high)
                    fib_tp_override = True
                if fib_ext_127 is not None and fib_ext_127 > price:
                    tp2 = max(tp2, fib_ext_127)
                    fib_tp_override = True
                if fib_ext_161 is not None and fib_ext_161 > price:
                    tp3 = max(tp3, fib_ext_161, tp2 + risk)
                    fib_tp_override = True
            elif bias == "bearish":
                if fib_swing_low is not None and fib_swing_low < price:
                    tp1 = min(tp1, fib_swing_low)
                    fib_tp_override = True
                if fib_ext_127 is not None and fib_ext_127 < price:
                    tp2 = min(tp2, fib_ext_127)
                    fib_tp_override = True
                if fib_ext_161 is not None and fib_ext_161 < price:
                    tp3 = min(tp3, fib_ext_161, tp2 - risk)
                    fib_tp_override = True

        rr = abs(tp2 - price) / risk if risk > 0 else 0.0
        hard_gate = (
            bool(news_clear)
            and bool(session_ok)
            and bool(institutional_volume_aligned)   # no trade on low volume / low liquidity
            and bias in ("bullish", "bearish")
            and rr >= MIN_RR_RATIO
            and not bool(adx_ranging)                # ADX < 20 => no-trade
        )

        if hard_gate and tier_a_candidate:
            state = "SIGNAL"
            tier = "TIER_A"
        elif hard_gate and tier_b_candidate:
            state = "SIGNAL"
            tier = "TIER_B"
        elif primary_confluence_count >= 2 and bias in ("bullish", "bearish") and bool(news_clear):
            state = "ALERT"
            tier = "TIER_B"
        else:
            state = "SCANNING"
            tier = "TIER_C"

        risk_mult = 1.0
        flags: List[str] = []
        if self._ctx.get("fakeout_risk"):
            risk_mult *= 0.5
            flags.append("H1 retests >= 4 (breakout/fakeout risk elevated).")
        elif self._ctx.get("h1_warning_retests"):
            risk_mult *= 0.75
            flags.append("H1 retests at 3 touches: caution before next test.")

        if daily_rejection_caution:
            risk_mult *= 0.7
            flags.append("Daily zone has weak retest count; skip intermediate move.")

        if zone_trend_mismatch:
            risk_mult *= 0.6
            flags.append("Price in S/D zone but EMA8/SMA18 trend suggests zone break risk.")

        if bias == "bullish" and in_demand and nearest_demand and str(nearest_demand.get("tf", "")) != "4H":
            risk_mult *= 0.8
            flags.append("Demand touch on smaller timeframe zone (higher fakeout risk than H4).")
        if bias == "bearish" and in_supply and nearest_supply and str(nearest_supply.get("tf", "")) != "4H":
            risk_mult *= 0.8
            flags.append("Supply touch on smaller timeframe zone (higher fakeout risk than H4).")

        if (in_demand and demand_rejection_wick) or (in_supply and supply_rejection_wick):
            risk_mult *= 0.85
            flags.append("Wick rejection while price enters zone: caution for chop/fakeout.")

        if (in_demand or in_supply) and not institutional_volume_aligned:
            risk_mult *= 0.75
            flags.append("Zone touch without volume confirmation.")
        elif zone_volume_confirm:
            flags.append("Zone + EMA/SMA + volume confluence confirmed.")

        if aligned_liquidity_grab:
            risk_mult = min(1.25, risk_mult * 1.05)
            flags.append("Liquidity grab/stop-hunt detected in setup direction (institutional reversal signature).")
        elif opposing_liquidity_grab:
            risk_mult *= 0.8
            flags.append("Recent liquidity grab opposes current bias.")
        if checklist.get("smc_signal_aligned"):
            smc_reasons = smc_ctx.get("reasons") or []
            if smc_reasons:
                flags.append(f"SMC aligned: {', '.join(smc_reasons[:4])}.")
        else:
            if bias == "bullish" and bool(smc_ctx.get("premium_zone")):
                risk_mult *= 0.9
                flags.append("SMC caution: bullish bias while price sits in premium zone.")
            if bias == "bearish" and bool(smc_ctx.get("discount_zone")):
                risk_mult *= 0.9
                flags.append("SMC caution: bearish bias while price sits in discount zone.")
        if chart_pattern_aligned and chart_patterns.get("primary") != "none":
            flags.append(
                f"Chart pattern confirmation: {chart_patterns.get('primary')} ({chart_pattern_direction})."
            )
        elif chart_patterns.get("primary") not in (None, "none") and chart_pattern_direction in ("bullish", "bearish"):
            flags.append(
                f"Chart pattern conflict: {chart_patterns.get('primary')} points {chart_pattern_direction}."
            )

        if fib_confluence_aligned:
            risk_mult = min(1.3, risk_mult * 1.08)
            if fib_nearest:
                flags.append(
                    f"Fibonacci aligned: near {fib_nearest.get('percentage')} ({round(float(fib_nearest.get('price', price)), 6)}) with multi-factor confluence."
                )
            if fib_high_prob:
                flags.append(f"Fib high-probability confluence signal(s): {len(fib_high_prob)}")
        else:
            if fib_nearest_key and not fib_zone_overlap:
                risk_mult *= 0.9
                flags.append("Key Fibonacci level nearby but missing zone/SR overlap.")
            if fib_active and not fib_bias_alignment:
                risk_mult *= 0.9
                flags.append("Fibonacci trend context conflicts with setup bias.")

        if fib_nearest and float(fib_nearest.get("ratio", -1)) == 0.786:
            risk_mult *= 0.88
            flags.append("Fibonacci 78.6% deep retracement touched (higher reversal risk).")
        if fib_tp_override:
            flags.append("Take-profit ladder aligned to Fibonacci swing/extension targets.")

        if bias == "bullish":
            if volume_ctx.get("accumulation"):
                risk_mult = min(1.25, risk_mult * 1.05)
                flags.append("Accumulation detected: consolidation at lows with rising volume.")
            if volume_ctx.get("up_price_down_volume_bearish") or volume_ctx.get("distribution"):
                risk_mult *= 0.8
                flags.append("Distribution/weak rally warning against bullish bias.")
        elif bias == "bearish":
            if volume_ctx.get("distribution"):
                risk_mult = min(1.25, risk_mult * 1.05)
                flags.append("Distribution detected: consolidation at highs with rising volume.")
            if volume_ctx.get("down_price_up_volume_bullish") or volume_ctx.get("accumulation"):
                risk_mult *= 0.8
                flags.append("Accumulation/institutional buying pressure against bearish bias.")

        if volume_ctx.get("partial_institutional_candle"):
            flags.append("Partial institutional candle structure observed (normal around bank activity zones).")

        for note in sl_hardening_notes:
            flags.append(note)

        if donchian_rsi_max_confluence:
            flags.append("Donchian(20) boundary touch + RSI extreme aligned (max confluence).")
        elif donchian_touch_aligned:
            flags.append("Donchian touch confirmed with secondary confluence (fakeout filter applied).")

        if macd_hist_switch_positive and bias == "bullish":
            flags.append("MACD histogram switched negative->positive (bullish momentum build).")
        if macd_hist_switch_negative and bias == "bearish":
            flags.append("MACD histogram switched positive->negative (bearish momentum build).")
        if macd_zero_cross_above and bias == "bullish":
            flags.append("MACD crossed above zero-line (buy bias support).")
        if macd_zero_cross_below and bias == "bearish":
            flags.append("MACD crossed below zero-line (sell bias support).")
        if osma_rising and bias == "bullish":
            flags.append("OsMA rising: bullish momentum strengthening.")
        if osma_falling and bias == "bearish":
            flags.append("OsMA falling: bearish momentum strengthening.")

        if bias == "bearish" and rsi_overbought:
            if at_resistance_context and bearish_candle_confirm:
                flags.append("RSI>75 at resistance with bearish candle: strong sell confluence.")
            elif at_resistance_context and not bearish_candle_confirm:
                risk_mult *= 0.85
                flags.append("RSI>75 at resistance but waiting bearish candle close.")
            elif (not at_resistance_context) and (bearish_div or bearish_candle_confirm):
                flags.append("RSI>75 away from resistance but divergence/candle supports sell watch.")
            else:
                risk_mult *= 0.8
                flags.append("RSI>75 without resistance/divergence confirmation: no blind sell.")

        if bias == "bullish" and rsi_oversold:
            if at_support_context and bullish_candle_confirm:
                flags.append("RSI<25 at support/demand with bullish candle: strong buy confluence.")
            elif at_support_context and not bullish_candle_confirm:
                risk_mult *= 0.85
                flags.append("RSI<25 at support/demand but waiting bullish candle close.")
            elif (not at_support_context) and (bullish_div or bullish_candle_confirm):
                flags.append("RSI<25 away from support but divergence/candle supports buy watch.")
            else:
                risk_mult *= 0.8
                flags.append("RSI<25 without support/divergence confirmation: no blind buy.")

        if divergence in ("bullish", "bearish") and not divergence_context_ok:
            risk_mult *= 0.85
            flags.append("Divergence detected but context weak (needs zone/trend confluence).")

        if bool(pattern.get("indecision", False)):
            risk_mult *= 0.85
            flags.append("Candlestick indecision (wicks on both sides): wait for the next close.")
        if candlestick_direction_aligned and bool(pattern.get("requires_confirmation", False)):
            risk_mult *= 0.9
            flags.append("Candlestick pattern is provisional; confirmation candle close required.")
        if candlestick_direction_aligned and at_key_zone and not bool(pattern.get("indecision", False)):
            flags.append("Candlestick aligned at key zone (3x relative candlestick bonus applied).")
        elif candlestick_direction_aligned:
            flags.append("Candlestick aligned as supporting confirmation (low dependency).")
        if candlestick_direction_aligned and bool(pattern.get("no_top_wick_note", False)):
            flags.append("Strong momentum candle with tiny/no top wick; continuation pressure present.")

        if bias == "bullish" and bool(arty.get("buy_arrow_raw")) and not arty_buy:
            risk_mult *= 0.9
            reasons = arty.get("buy_block_reasons") or []
            flags.append(f"ARTY raw BUY arrow filtered as fake: {'; '.join(reasons[:3])}")
        if bias == "bearish" and bool(arty.get("sell_arrow_raw")) and not arty_sell:
            risk_mult *= 0.9
            reasons = arty.get("sell_block_reasons") or []
            flags.append(f"ARTY raw SELL arrow filtered as fake: {'; '.join(reasons[:3])}")
        if (bias == "bullish" and arty_buy) or (bias == "bearish" and arty_sell):
            if (bias == "bullish" and arty.get("engulfing_or_strike_boost_bull")) or (
                bias == "bearish" and arty.get("engulfing_or_strike_boost_bear")
            ):
                risk_mult = min(1.25, risk_mult * 1.05)
                flags.append("ARTY arrow confirmed with engulfing/3-line-strike boost.")
            else:
                flags.append("ARTY arrow validated by trend + RSI + macro filters.")

        if flip_buy_ok and bias == "bullish":
            flags.append(f"Zone flip confirmed (resistance->support), strength={round(flip_buy_strength, 2)}")
        if flip_sell_ok and bias == "bearish":
            flags.append(f"Zone flip confirmed (support->resistance), strength={round(flip_sell_strength, 2)}")
        impact = str(news_impact or "").lower()
        if impact in ("medium", "orange"):
            risk_mult *= 0.5
            flags.append("Medium-impact news nearby.")
        if impact in ("high", "red"):
            risk_mult = 0.0
            flags.append("High-impact news lockout.")
            state = "SCANNING"
        if adx_ranging:
            if extreme_range_setup:
                flags.append("ADX below 20 (ranging), but extreme range setup qualifies (Donchian+RSI+zone).")
            else:
                flags.append("ADX below 20 (ranging market): breakout-style setups blocked.")
            risk_mult *= 0.7
        elif adx_trending:
            flags.append("ADX above 25 with DI alignment: strong trend condition.")

        if bias == "bullish" and swing_buy_ready:
            flags.append("Swing BUY module aligned: EMA9>EMA50>EMA90 + SAR 3+ dots below + RSI>50 + price>EMA200.")
        if bias == "bearish" and swing_sell_ready:
            flags.append("Swing SELL module aligned: EMA50>EMA9 and EMA50<EMA90 + SAR 3+ dots above + RSI<50 + price<EMA200.")

        prev_close = float(candles[-2]["close"]) if len(candles) >= 2 else None
        crossed_over_resistance = bool(
            nr and prev_close is not None and prev_close <= float(nr["level"]) and price > float(nr["level"])
        )
        crossed_under_support = bool(
            ns and prev_close is not None and prev_close >= float(ns["level"]) and price < float(ns["level"])
        )

        missing = [self.MISSING_HINTS[k] for k in required_checks if not checklist.get(k)]
        if not checklist.get("candlestick_pattern_aligned"):
            missing.append("Optional candlestick confirmation not aligned yet.")
        proximity = round((fired / max(float(primary_confluence_total), 1.0)) * 100.0, 1)

        return {
            "state": state,
            "tier": tier,
            "quality": quality,
            "bias": bias,
            "setup_proximity": proximity,
            "confluence_fired": round(fired, 2),
            "confluence_total": round(confluence_total_primary, 2),
            "confluence_required_fired": fired_required,
            "confluence_optional_bonus": round(candlestick_bonus, 3),
            "checklist": checklist,
            "missing_conditions": missing,
            "risk_multiplier": round(risk_mult, 3),
            "risk_flags": flags,
            "levels": {
                "nearest_support": ns,
                "nearest_resistance": nr,
                "nearest_demand": nearest_demand,
                "nearest_supply": nearest_supply,
                "h4_zone_priority_ok": h4_zone_priority_ok,
                "zone_priority_pass": zone_priority_pass,
                "demand_zone_hit": in_demand,
                "supply_zone_hit": in_supply,
                "demand_wick_rejection": demand_rejection_wick,
                "supply_wick_rejection": supply_rejection_wick,
                "zone_trend_mismatch": zone_trend_mismatch,
                "rsi_overbought": rsi_overbought,
                "rsi_oversold": rsi_oversold,
                "rsi_sell_ready": rsi_sell_ready,
                "rsi_buy_ready": rsi_buy_ready,
                "rsi_at_resistance_context": at_resistance_context,
                "rsi_at_support_context": at_support_context,
                "divergence_context_ok": divergence_context_ok,
                "zone_flip_buy_confirmed": flip_buy_ok,
                "zone_flip_sell_confirmed": flip_sell_ok,
                "daily_zone_rejection_caution": daily_rejection_caution,
                "at_key_zone": at_key_zone,
                "candlestick_confirmed": candlestick_confirmed,
                "candlestick_bonus": round(candlestick_bonus, 3),
                "h1_retests": self._ctx.get("h1_retests", 0),
                "h1_warning_retests": self._ctx.get("h1_warning_retests", False),
                "fakeout_risk": self._ctx.get("fakeout_risk", False),
                "resistance_broken_alert": crossed_over_resistance,
                "support_broken_alert": crossed_under_support,
                "buy_side_stop_hunt": buy_side_stop_hunt,
                "sell_side_stop_hunt": sell_side_stop_hunt,
                "aligned_liquidity_grab": aligned_liquidity_grab,
                "opposing_liquidity_grab": opposing_liquidity_grab,
                "swept_buy_level": liquidity.get("swept_buy_level"),
                "swept_sell_level": liquidity.get("swept_sell_level"),
                "smc_choch": smc_ctx.get("choch"),
                "smc_bos": smc_ctx.get("bos"),
                "smc_bullish_order_block": smc_ctx.get("bullish_order_block"),
                "smc_bearish_order_block": smc_ctx.get("bearish_order_block"),
                "smc_in_bullish_order_block": smc_ctx.get("in_bullish_order_block"),
                "smc_in_bearish_order_block": smc_ctx.get("in_bearish_order_block"),
                "smc_equal_highs": smc_ctx.get("equal_highs"),
                "smc_equal_lows": smc_ctx.get("equal_lows"),
                "smc_near_equal_highs": smc_ctx.get("near_equal_highs"),
                "smc_near_equal_lows": smc_ctx.get("near_equal_lows"),
                "smc_range_high": smc_ctx.get("range_high"),
                "smc_range_low": smc_ctx.get("range_low"),
                "smc_equilibrium": smc_ctx.get("equilibrium"),
                "smc_premium_zone": smc_ctx.get("premium_zone"),
                "smc_discount_zone": smc_ctx.get("discount_zone"),
                "smc_bias_zone_alignment": smc_ctx.get("bias_zone_alignment"),
                "smc_bullish_score": smc_ctx.get("bullish_score"),
                "smc_bearish_score": smc_ctx.get("bearish_score"),
                "smc_signal_reasons": smc_ctx.get("reasons", []),
                "fib_active_timeframe": fib_active.get("timeframe") if fib_active else None,
                "fib_trend": fib_active_trend if fib_active else "ranging",
                "fib_swing_high": fib_active.get("swing_high") if fib_active else None,
                "fib_swing_low": fib_active.get("swing_low") if fib_active else None,
                "fib_retracement_levels": fib_retr_levels,
                "fib_extension_levels": fib_ext_levels,
                "fib_nearest_level": fib_nearest,
                "fib_confluence_zones": (fib_active.get("confluence_zones") or [])[:5] if fib_active else [],
                "fib_high_probability_signals": fib_high_prob,
                "fib_zone_overlap": fib_zone_overlap,
                "fib_bias_alignment": fib_bias_alignment,
                "fib_tp_override": fib_tp_override,
                "stop_loss_hardened": bool(sl_hardening_notes),
                "stop_loss_hardening_notes": sl_hardening_notes,
                "donchian_buy_touch": donchian_buy_touch,
                "donchian_sell_touch": donchian_sell_touch,
                "donchian_rsi_max_confluence": donchian_rsi_max_confluence,
                "extreme_range_setup": extreme_range_setup,
                "adx_trending": adx_trending,
                "adx_ranging": adx_ranging,
                "swing_buy_ready": swing_buy_ready,
                "swing_sell_ready": swing_sell_ready,
            },
            "indicators": {
                "rsi14": rsi14,
                "rsi_length": RSI_LENGTH,
                "rsi_upper_band": RSI_UPPER_BAND,
                "rsi_lower_band": RSI_LOWER_BAND,
                "rsi_swing_mid": RSI_SWING_MID,
                "rsi_swing_buy_zone": rsi_swing_buy_zone,
                "rsi_swing_sell_zone": rsi_swing_sell_zone,
                "rsi_divergence": divergence,
                "macd_line": macd_line,
                "macd_signal": macd_signal,
                "macd_hist": mch,
                "macd_hist_prev": mch_prev,
                "macd_hist_switch_positive": macd_hist_switch_positive,
                "macd_hist_switch_negative": macd_hist_switch_negative,
                "macd_zero_cross_above": macd_zero_cross_above,
                "macd_zero_cross_below": macd_zero_cross_below,
                "osma": osma,
                "osma_prev": osma_prev,
                "osma_rising": osma_rising,
                "osma_falling": osma_falling,
                "adx14": adx14,
                "pdi": pdi,
                "mdi": mdi,
                "adx_trending": adx_trending,
                "adx_ranging": adx_ranging,
                "ema8": e8,
                "sma18": s18,
                "ema8_sma18_state": "uptrend" if ema_sma_up else ("downtrend" if ema_sma_down else "flat"),
                "ema9": e9,
                "ema50": e50,
                "ema90": e90,
                "ema9_above_ema50": ema9_above_ema50,
                "ema50_above_ema9": ema50_above_ema9,
                "ema50_above_ema90": ema50_above_ema90,
                "ema50_below_ema90": ema50_below_ema90,
                "ema200": e200,
                "ema200_macro_bias": macro_bias_ema200,
                "smma21": sm21,
                "smma50": sm50,
                "smma100": sm100,
                "smma200": sm200,
                "smma200_prev": sm200_prev,
                "smma200_dynamic_state": arty.get("sm200_dynamic_state"),
                "arty_cloud_state": arty.get("cloud_state"),
                "arty_buy_arrow_raw": bool(arty.get("buy_arrow_raw")),
                "arty_sell_arrow_raw": bool(arty.get("sell_arrow_raw")),
                "arty_buy_arrow": arty_buy,
                "arty_sell_arrow": arty_sell,
                "arty_touched_lines_buy": arty.get("touched_lines_buy", []),
                "arty_touched_lines_sell": arty.get("touched_lines_sell", []),
                "arty_buy_block_reasons": arty.get("buy_block_reasons", []),
                "arty_sell_block_reasons": arty.get("sell_block_reasons", []),
                "arty_engulfing_or_strike_boost_bull": bool(arty.get("engulfing_or_strike_boost_bull")),
                "arty_engulfing_or_strike_boost_bear": bool(arty.get("engulfing_or_strike_boost_bear")),
                "arty_choppy_market": bool(arty.get("choppy_market")),
                "arty_continuation_break_buy": bool(arty.get("continuation_break_buy")),
                "arty_continuation_break_sell": bool(arty.get("continuation_break_sell")),
                "parabolic_sar": sar_val,
                "parabolic_sar_side": sar_side,
                "parabolic_sar_streak": sar_streak,
                "parabolic_sar_start": SAR_START,
                "parabolic_sar_increment": SAR_INCREMENT,
                "parabolic_sar_maximum": SAR_MAXIMUM,
                "parabolic_sar_buy_ready": sar_buy_ready,
                "parabolic_sar_sell_ready": sar_sell_ready,
                "atr14": a14,
                "donchian_upper": up,
                "donchian_lower": low,
                "donchian_basis": donchian_basis,
                "donchian_buy_touch": donchian_buy_touch,
                "donchian_sell_touch": donchian_sell_touch,
                "donchian_rsi_max_confluence": donchian_rsi_max_confluence,
                "pattern": pattern["name"],
                "pattern_strength": pattern["strength"],
                "pattern_all_matches": pattern.get("all_matches", []),
                "pattern_requires_confirmation": bool(pattern.get("requires_confirmation", False)),
                "pattern_indecision": bool(pattern.get("indecision", False)),
                "pattern_no_top_wick_note": bool(pattern.get("no_top_wick_note", False)),
                "candlestick_direction_aligned": candlestick_direction_aligned,
                "candlestick_confirmed": candlestick_confirmed,
                "candlestick_bonus": round(candlestick_bonus, 3),
                "fvg": fvg,
                "smc_signal_aligned": bool(smc_ctx.get("smc_signal_aligned")),
                "smc_reasons": smc_ctx.get("reasons", []),
                "smc_choch": smc_ctx.get("choch"),
                "smc_bos": smc_ctx.get("bos"),
                "smc_bullish_order_block": smc_ctx.get("bullish_order_block"),
                "smc_bearish_order_block": smc_ctx.get("bearish_order_block"),
                "smc_in_bullish_order_block": smc_ctx.get("in_bullish_order_block"),
                "smc_in_bearish_order_block": smc_ctx.get("in_bearish_order_block"),
                "smc_equal_highs": smc_ctx.get("equal_highs"),
                "smc_equal_lows": smc_ctx.get("equal_lows"),
                "smc_near_equal_highs": smc_ctx.get("near_equal_highs"),
                "smc_near_equal_lows": smc_ctx.get("near_equal_lows"),
                "smc_range_high": smc_ctx.get("range_high"),
                "smc_range_low": smc_ctx.get("range_low"),
                "smc_equilibrium": smc_ctx.get("equilibrium"),
                "smc_premium_zone": smc_ctx.get("premium_zone"),
                "smc_discount_zone": smc_ctx.get("discount_zone"),
                "smc_bullish_score": smc_ctx.get("bullish_score"),
                "smc_bearish_score": smc_ctx.get("bearish_score"),
                "fib_h1_ready": bool(fib_h1.get("ready")) if fib_h1 else False,
                "fib_h4_ready": bool(fib_h4.get("ready")) if fib_h4 else False,
                "fib_active_timeframe": fib_active.get("timeframe") if fib_active else None,
                "fib_trend": fib_active_trend if fib_active else "ranging",
                "fib_retracement_levels": fib_retr_levels,
                "fib_extension_levels": fib_ext_levels,
                "fib_nearest_level": fib_nearest,
                "fib_nearest_percentage": fib_nearest.get("percentage") if fib_nearest else None,
                "fib_nearest_price": fib_nearest.get("price") if fib_nearest else None,
                "fib_nearest_distance_pips": fib_nearest.get("distance_pips") if fib_nearest else None,
                "fib_confluence_strength": float(fib_confluence_top.get("strength", 0.0)) if fib_confluence_top else 0.0,
                "fib_high_probability_count": len(fib_high_prob),
                "fibonacci_confluence_aligned": fib_confluence_aligned,
                "chart_pattern_primary": chart_patterns.get("primary"),
                "chart_pattern_direction": chart_patterns.get("direction"),
                "chart_pattern_strength": chart_patterns.get("strength"),
                "chart_pattern_matches": [
                    m.get("name")
                    for m in (chart_patterns.get("matches") or [])
                ],
                "chart_pattern_aligned_with_bias": chart_pattern_aligned,
                "rsi_overbought": rsi_overbought,
                "rsi_oversold": rsi_oversold,
                "rsi_sell_ready": rsi_sell_ready,
                "rsi_buy_ready": rsi_buy_ready,
                "at_resistance_context": at_resistance_context,
                "at_support_context": at_support_context,
                "divergence_context_ok": divergence_context_ok,
                "last_high": highs[-1] if highs else None,
                "last_low": lows[-1] if lows else None,
                "volume_price_trend": volume_ctx.get("price_trend"),
                "volume_trend": volume_ctx.get("volume_trend"),
                "down_price_up_volume_bullish": bool(volume_ctx.get("down_price_up_volume_bullish")),
                "up_price_down_volume_bearish": bool(volume_ctx.get("up_price_down_volume_bearish")),
                "accumulation": bool(volume_ctx.get("accumulation")),
                "distribution": bool(volume_ctx.get("distribution")),
                "green_triangle_bottom": bool(volume_ctx.get("green_triangle_bottom")),
                "red_triangle_top": bool(volume_ctx.get("red_triangle_top")),
                "partial_institutional_candle": bool(volume_ctx.get("partial_institutional_candle")),
                "institutional_volume_aligned": institutional_volume_aligned,
                "swing_buy_ready": swing_buy_ready,
                "swing_sell_ready": swing_sell_ready,
            },
            "trade_plan": {
                "direction": "BUY" if bias == "bullish" else ("SELL" if bias == "bearish" else "WAIT"),
                "entry_type": "market",
                "entry": round(price, 6),
                "sl": round(sl, 6),
                "tp1": round(tp1, 6),
                "tp2": round(tp2, 6),
                "tp3": round(tp3, 6),
                "rr": round(rr, 3),
            },
        }
