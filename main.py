import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List

from config.settings import MIN_RR_RATIO, OANDA_ACCOUNT_ID, OANDA_API_KEY, OANDA_ENV, PAIRS, RISK_PERCENT
from engine.candles import CandleBuilder
from engine.entry import EntryEngine
from engine.liquidity import LiquidityEngine
from engine.mtf_bias import MTFBiasEngine
from engine.news import NewsEngine
from engine.performance_memory import PerformanceMemory
from engine.risk import RiskEngine
from engine.sessions import SessionEngine
from engine.setup_detector import SetupDetector
from engine.structure import StructureEngine
from engine.trend_filter import TrendFilter
from engine.volume_filter import VolumeFilter
from output.signal_logger import SignalLogger


PRICES_PATH = os.path.join("output", "prices.json")
ANALYSIS_PATH = os.path.join("output", "analysis_states.json")
SIGNAL_COOLDOWN_SECONDS = 15 * 60
ANALYSIS_PRINT_SECONDS = 60
NEWS_REFRESH_SECONDS = 30 * 60
PERFORMANCE_REFRESH_SECONDS = 5 * 60


def print_banner(balance: float, leverage: int) -> None:
    print()
    print("=" * 72)
    print("AI TRADING SIGNAL ENGINE v3.0")
    print("-" * 72)
    print(f"Broker       : OANDA ({OANDA_ENV})")
    print(f"Pairs        : {', '.join(PAIRS)}")
    print(f"Balance      : ${balance:.2f}")
    print(f"Leverage     : 1:{leverage}")
    print(f"Risk / Trade : {int(RISK_PERCENT * 100)}% (base)")
    print("Mode         : Signal-only (no auto execution)")
    print("=" * 72)
    print()


def get_user_inputs():
    print("=== Trading AI Setup ===")
    print("Using defaults: $10,000 and 1:100 leverage.")
    balance, leverage = 10000.0, 100
    return balance, leverage


def _safe_write_json(path: str, payload: Dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, default=str)
    os.replace(tmp, path)


def _confidence_from_analysis(analysis: Dict) -> float:
    tier = str(analysis.get("tier", "TIER_C")).upper()
    quality = str(analysis.get("quality", "NO_TRADE")).upper()
    if quality == "PLATINUM":
        return 1.15
    if quality == "GOLD":
        return 1.05
    if tier == "TIER_A":
        return 1.0
    if tier == "TIER_B":
        return 0.9
    return 0.75


def _pip_size(pair: str) -> float:
    p = pair.upper().replace("/", "_")
    if p.endswith("JPY"):
        return 0.01
    if p.startswith("XAU") or p.startswith("XAG"):
        return 0.1
    if p.startswith("BTC"):
        return 1.0
    return 0.0001


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _is_reversal_catch(signal: Dict, analysis: Dict) -> bool:
    direction = str(signal.get("direction", "")).upper()
    checklist = analysis.get("checklist", {}) or {}
    indicators = analysis.get("indicators", {}) or {}
    levels = analysis.get("levels", {}) or {}

    rsi14 = _safe_float(indicators.get("rsi14"), 50.0)
    divergence = str(indicators.get("rsi_divergence", "none")).lower()
    at_major_zone = bool(
        levels.get("at_key_zone")
        or checklist.get("sr_zone_aligned")
        or checklist.get("supply_demand_zone_aligned")
    )
    candle_confirmed = bool(checklist.get("candlestick_pattern_aligned"))

    if direction == "BUY":
        return bool(rsi14 <= 25.0 and divergence == "bullish" and at_major_zone and candle_confirmed)
    if direction == "SELL":
        return bool(rsi14 >= 75.0 and divergence == "bearish" and at_major_zone and candle_confirmed)
    return False


def _apply_reversal_protocol(signal: Dict, analysis: Dict, pair: str) -> Dict:
    updated = dict(signal)
    direction = str(signal.get("direction", "")).upper()
    entry = _safe_float(signal.get("entry"))
    sl = _safe_float(signal.get("sl"), entry)
    pip = _pip_size(pair)
    levels = analysis.get("levels", {}) or {}

    if direction not in ("BUY", "SELL"):
        return updated

    quick_1 = 20.0 * pip
    quick_2 = 30.0 * pip

    if direction == "BUY":
        tp1 = entry + quick_1
        tp2 = entry + quick_2
        nr = levels.get("nearest_resistance") or {}
        zone_tp = _safe_float(nr.get("level"), 0.0)
        tp3 = zone_tp if zone_tp > entry else tp2
    else:
        tp1 = entry - quick_1
        tp2 = entry - quick_2
        ns = levels.get("nearest_support") or {}
        zone_tp = _safe_float(ns.get("level"), 0.0)
        tp3 = zone_tp if 0.0 < zone_tp < entry else tp2

    risk = abs(entry - sl)
    rr = abs(tp2 - entry) / risk if risk > 0 else 0.0

    if rr < MIN_RR_RATIO:
        return updated

    updated["tp1"] = round(tp1, 6)
    updated["tp2"] = round(tp2, 6)
    updated["tp3"] = round(tp3, 6)
    updated["tp"] = round(tp2, 6)
    updated["rr"] = round(rr, 3)
    return updated


def _build_continuous_loop(
    pair: str,
    analysis: Dict,
    news_window: Dict,
    session_ok: bool,
    has_open_trade: bool,
    execute_ready: bool,
    performance_summary: Dict,
) -> Dict:
    checklist = analysis.get("checklist", {}) or {}
    levels = analysis.get("levels", {}) or {}
    indicators = analysis.get("indicators", {}) or {}
    missing = analysis.get("missing_conditions", []) or []
    score = f"{analysis.get('confluence_fired', 0)}/{analysis.get('confluence_total', 0)}"
    review = (performance_summary.get("weekly_audit") or {}).get("recent_weeks", [])

    step_state = [
        {"step": 1, "name": "CHECK Macro/News", "ok": bool(checklist.get("fundamental_news_clear")), "note": str(news_window.get("status", ""))},
        {"step": 2, "name": "SCAN 1W/1D/4H", "ok": bool(checklist.get("weekly_structure_aligned") or checklist.get("daily_structure_aligned") or checklist.get("h4_structure_aligned")), "note": str(analysis.get("bias", "neutral"))},
        {"step": 3, "name": "IDENTIFY Zones", "ok": bool(levels.get("nearest_support") or levels.get("nearest_resistance") or levels.get("nearest_demand") or levels.get("nearest_supply")), "note": "S/R + S/D mapped"},
        {"step": 4, "name": "CHECK Zone Proximity", "ok": bool(levels.get("at_key_zone") or levels.get("demand_zone_hit") or levels.get("supply_zone_hit")), "note": "Approach/at-zone check"},
        {"step": 5, "name": "DRILL 1H + Volume + SMC", "ok": bool(checklist.get("volume_institutional_aligned") and checklist.get("smc_signal_aligned")), "note": "Volume+SMC validation"},
        {"step": 6, "name": "SCORE Confluence", "ok": True, "note": score},
        {"step": 7, "name": "MONITOR RSI + Divergence", "ok": bool(checklist.get("rsi_level_direction_aligned") or checklist.get("rsi_divergence_aligned")), "note": f"RSI={round(_safe_float(indicators.get('rsi14'), 0.0), 2)}"},
        {"step": 8, "name": "CANDLESTICK Confirm", "ok": bool(checklist.get("candlestick_pattern_aligned")), "note": str(indicators.get("pattern", "none"))},
        {"step": 9, "name": "EVALUATE Session + News", "ok": bool(session_ok and checklist.get("fundamental_news_clear")), "note": "Session/news gate"},
        {"step": 10, "name": "DECIDE Tier", "ok": True, "note": f"{analysis.get('tier', 'TIER_C')} / {analysis.get('state', 'SCANNING')}"},
        {"step": 11, "name": "EXECUTE", "ok": bool(execute_ready), "note": "Signal ready" if execute_ready else "Waiting confluence"},
        {"step": 12, "name": "MANAGE", "ok": bool(has_open_trade), "note": "SL->BE / trail active" if has_open_trade else "No open trade"},
        {"step": 13, "name": "LOG Journal", "ok": True, "note": "Journal pipeline active"},
        {"step": 14, "name": "REVIEW Stats", "ok": True, "note": f"weekly_samples={len(review)}"},
        {"step": 15, "name": "LOOP BACK", "ok": True, "note": f"{pair} continuous cycle"},
    ]

    return {
        "version": "1.0",
        "step_states": step_state,
        "missing_conditions": missing,
    }


def _institutional_open_window() -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    mins = now.hour * 60 + now.minute
    windows = [
        (7 * 60, 7 * 60 + 30, "LONDON_OPEN_WINDOW"),
        (13 * 60 + 30, 14 * 60, "NEWYORK_OPEN_WINDOW"),
    ]
    for start, end, label in windows:
        if start <= mins < end:
            return True, label
    return False, ""


def main():
    try:
        from broker.oanda import OandaBroker
    except Exception as exc:
        print(f"Missing runtime dependency for broker streaming: {exc}")
        print("Install project dependencies before running main.py.")
        sys.exit(1)

    balance, leverage = get_user_inputs()
    print_banner(balance, leverage)

    if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
        print("OANDA credentials missing. Add OANDA_API_KEY and OANDA_ACCOUNT_ID in .env.")
        sys.exit(1)

    logger = SignalLogger()
    news = NewsEngine()
    mtf = MTFBiasEngine()
    session = SessionEngine()
    perf_memory = PerformanceMemory()
    perf_memory.refresh(force=True)
    last_performance_refresh = time.time()

    print("Fetching economic calendar...")
    news.fetch_news()
    last_news_refresh = time.time()

    print("Loading multi-timeframe bias (H4 / H1)...")
    mtf.start()

    engines = {}
    last_prices = {pair: 0.0 for pair in PAIRS}
    analysis_states: Dict[str, Dict] = {pair: {} for pair in PAIRS}
    last_state_by_pair = {pair: None for pair in PAIRS}
    last_state_print_ts = {pair: 0.0 for pair in PAIRS}
    last_signal_ts = {pair: 0.0 for pair in PAIRS}
    last_signal_dir = {pair: None for pair in PAIRS}
    open_trades: Dict[str, List[Dict]] = {pair: [] for pair in PAIRS}
    last_price_export = 0.0

    for pair in PAIRS:
        engines[pair] = {
            "candles": CandleBuilder(),
            "structure": StructureEngine(),
            "liquidity": LiquidityEngine(),
            "entry": EntryEngine(),
            "risk": RiskEngine(balance, risk_percent=RISK_PERCENT),
            "trend": TrendFilter(),
            "volume": VolumeFilter(),
            "detector": SetupDetector(pair=pair),
        }

    print()
    print("Waiting for live ticks. Monitoring:", ", ".join(PAIRS))
    print("-" * 72)

    def update_open_trades(pair: str, price: float) -> None:
        trades = open_trades.get(pair, [])
        if not trades:
            return

        survivors: List[Dict] = []
        pip = _pip_size(pair)
        engine = engines.get(pair)
        if engine is None:
            return

        for tr in trades:
            direction = str(tr.get("direction", "")).upper()
            entry = float(tr.get("entry", 0.0))
            sl = float(tr.get("sl", 0.0))
            tp1 = float(tr.get("tp1", tr.get("tp2", 0.0)))
            tp2 = float(tr.get("tp2", tr.get("tp3", 0.0)))
            tp3 = float(tr.get("tp3", tr.get("tp2", 0.0)))
            lot = float(tr.get("lot_size", 0.0))
            be_armed = bool(tr.get("be_armed", False))
            tp1_hit = bool(tr.get("tp1_hit", False))
            tp2_hit = bool(tr.get("tp2_hit", False))
            trade_id = str(tr.get("trade_id", ""))

            result = None
            exit_price = None

            if direction == "BUY":
                if (not tp1_hit) and price >= tp1:
                    tr["tp1_hit"] = True
                    tr["be_armed"] = True
                    tr["sl"] = entry
                    sl = entry
                    be_armed = True
                    tp1_hit = True
                if tp1_hit and (not tp2_hit) and price >= tp2:
                    tr["tp2_hit"] = True
                    tr["sl"] = max(float(tr.get("sl", sl)), tp1)
                    sl = float(tr["sl"])
                    tp2_hit = True

                if price <= sl:
                    exit_price = sl
                    if tp2_hit:
                        result = "WIN"
                    elif be_armed and abs(sl - entry) <= (pip * 0.2):
                        result = "BREAKEVEN"
                    else:
                        result = "LOSS"
                elif price >= tp3:
                    exit_price = tp3
                    result = "WIN"
            elif direction == "SELL":
                if (not tp1_hit) and price <= tp1:
                    tr["tp1_hit"] = True
                    tr["be_armed"] = True
                    tr["sl"] = entry
                    sl = entry
                    be_armed = True
                    tp1_hit = True
                if tp1_hit and (not tp2_hit) and price <= tp2:
                    tr["tp2_hit"] = True
                    tr["sl"] = min(float(tr.get("sl", sl)), tp1)
                    sl = float(tr["sl"])
                    tp2_hit = True

                if price >= sl:
                    exit_price = sl
                    if tp2_hit:
                        result = "WIN"
                    elif be_armed and abs(sl - entry) <= (pip * 0.2):
                        result = "BREAKEVEN"
                    else:
                        result = "LOSS"
                elif price <= tp3:
                    exit_price = tp3
                    result = "WIN"
            else:
                survivors.append(tr)
                continue

            if result is None or exit_price is None:
                survivors.append(tr)
                continue

            if direction == "BUY":
                pnl_price = float(exit_price) - entry
            else:
                pnl_price = entry - float(exit_price)
            pnl_pips = pnl_price / max(pip, 1e-9)
            # For USD-base pairs (USD/JPY, USD/CHF, USD/CAD), pnl_price is in the
            # quote currency. Divide by exit_price to convert to USD.
            # For USD-quote pairs (EUR/USD, GBP/USD, XAU/USD etc.) pnl_price is
            # already in USD — multiply by position size directly.
            _pair_code = str(pair or "").upper().replace("/", "_")
            _usd_base = _pair_code.startswith("USD_") and "XAU" not in _pair_code and "XAG" not in _pair_code
            if _usd_base:
                pnl_usd = (pnl_price * lot) / max(float(exit_price), 1e-9)
            else:
                pnl_usd = pnl_price * lot

            try:
                engine["risk"].register_trade_result(pnl_usd)
            except Exception:
                pass

            logger.update_trade_result(
                trade_id=trade_id,
                result=result,
                pnl_pips=pnl_pips,
                pnl_usd=pnl_usd,
                what_right="Followed TP/SL playbook and respected risk plan." if result in ("WIN", "BREAKEVEN") else "",
                what_wrong="Entry invalidated before target ladder completed." if result == "LOSS" else "",
                take_again="YES" if result in ("WIN", "BREAKEVEN") else "NO",
                emotion_note="System-managed execution",
            )
            try:
                perf_memory.refresh(force=True)
            except Exception:
                pass
            print(
                f"[JOURNAL] {pair} {direction} closed | result={result} "
                f"| exit={round(float(exit_price), 6)} | pnl_pips={round(float(pnl_pips), 2)} | pnl_usd={round(float(pnl_usd), 2)}"
            )

        open_trades[pair] = survivors

    def on_tick(tick):
        nonlocal last_price_export, last_news_refresh, last_performance_refresh
        try:
            pair = tick.get("instrument")
            engine = engines.get(pair)
            if engine is None:
                return

            bids = tick.get("bids") or []
            asks = tick.get("asks") or []
            if not bids or not asks:
                return
            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])
            price = (bid + ask) / 2.0
            last_prices[pair] = round(price, 6)
            update_open_trades(pair, price)

            if time.time() - last_price_export > 1.0:
                _safe_write_json(PRICES_PATH, last_prices)
                last_price_export = time.time()

            if time.time() - last_news_refresh > NEWS_REFRESH_SECONDS:
                try:
                    news.fetch_news()
                except Exception:
                    pass
                last_news_refresh = time.time()
            if time.time() - last_performance_refresh > PERFORMANCE_REFRESH_SECONDS:
                try:
                    perf_memory.refresh(force=True)
                except Exception:
                    pass
                last_performance_refresh = time.time()

            candle = engine["candles"].update(tick["time"], price)
            if candle is None:
                return

            history = engine["candles"].get_history()
            structure = engine["structure"].update(candle)
            liquidity = engine["liquidity"].update(candle, structure)
            trend = engine["trend"].get_trend(history)
            volume_ok = engine["volume"].is_volume_confirmed(candle, history)
            volume_ctx = engine["volume"].institutional_context(candle, history, pair=pair)
            mtf_bias = mtf.get_bias(pair)
            session_ok, session_reason = session.can_trade_now()
            session_name = session.current_session()
            news_window = news.evaluate_trade_window(pair)
            news_clear = not bool(news_window.get("block", False))
            open_window_active, open_window_label = _institutional_open_window()

            analysis = engine["detector"].analyze(
                candle=candle,
                history=history,
                structure=structure,
                liquidity=liquidity,
                trend=trend,
                volume_ok=volume_ok,
                mtf_bias=mtf_bias,
                volume_context=volume_ctx,
                session_ok=session_ok,
                news_clear=news_clear,
                news_impact=str(news_window.get("impact", "NONE")),
            )
            signal = engine["entry"].check(
                candle=candle,
                structure=structure,
                liquidity=liquidity,
                trend=trend,
                volume_ok=volume_ok,
                mtf_bias=mtf_bias,
                analysis=analysis,
            )
            performance_summary = perf_memory.get_summary()
            analysis_loop = _build_continuous_loop(
                pair=pair,
                analysis=analysis,
                news_window=news_window,
                session_ok=session_ok,
                has_open_trade=bool(open_trades.get(pair)),
                execute_ready=bool(signal),
                performance_summary=performance_summary,
            )
            pair_behavior = (performance_summary.get("pair_behavior") or {}).get(pair, {})
            weekly_audit = performance_summary.get("weekly_audit") or {}

            analysis_states[pair] = {
                "pair": pair,
                "timestamp": time.time(),
                "session": session_name,
                "session_reason": session_reason,
                "news_status": news_window.get("status", ""),
                "institutional_timing": {
                    "open_window_active": open_window_active,
                    "open_window_label": open_window_label,
                },
                "continuous_analysis_loop": analysis_loop,
                "review_memory": {
                    "pair_behavior": pair_behavior,
                    "top_pattern_memory": (performance_summary.get("top_pattern_memory") or [])[:5],
                    "session_analysis": (performance_summary.get("session_analysis") or {}).get(pair, {}),
                    "weekly_audit": weekly_audit,
                },
                **analysis,
            }
            _safe_write_json(ANALYSIS_PATH, analysis_states)

            state = analysis.get("state")
            now_ts = time.time()
            if (
                state != last_state_by_pair[pair]
                or (now_ts - last_state_print_ts[pair]) > ANALYSIS_PRINT_SECONDS
            ):
                prox = analysis.get("setup_proximity", 0.0)
                fired = analysis.get("confluence_fired", 0)
                total = analysis.get("confluence_total", 0)
                bias = analysis.get("bias", "neutral")
                print(
                    f"[{pair}] {state} | bias={bias} | proximity={prox}% | "
                    f"confluence={fired}/{total} | news={news_window.get('impact','NONE')} | session={session_name}"
                )
                last_state_by_pair[pair] = state
                last_state_print_ts[pair] = now_ts

            if signal is None:
                return

            direction = signal.get("direction")
            if open_trades.get(pair):
                # One active position per pair to keep journal/result lifecycle deterministic.
                return
            if (
                direction == last_signal_dir[pair]
                and (now_ts - last_signal_ts[pair]) < SIGNAL_COOLDOWN_SECONDS
            ):
                return

            can_trade_today, risk_reason = engine["risk"].can_trade_today()
            if not can_trade_today:
                print(f"[{pair}] Risk gate blocked signal: {risk_reason}")
                return
            if perf_memory.pause_recommended():
                print(f"[{pair}] Weekly self-audit pause active: win-rate <50% for 2 consecutive weeks.")
                return

            reversal_trade = _is_reversal_catch(signal, analysis)
            if reversal_trade:
                signal = _apply_reversal_protocol(signal, analysis, pair)
                cond = signal.get("conditions") or {}
                rf = list(cond.get("risk_flags") or [])
                rf.append("Reversal catch protocol: over-extended RSI + divergence + major zone + candle confirm.")
                cond["risk_flags"] = rf
                signal["conditions"] = cond
            if open_window_active:
                cond = signal.get("conditions") or {}
                rf = list(cond.get("risk_flags") or [])
                rf.append(f"Institutional open timing caution: {open_window_label} (watch initial stop-hunt sweep).")
                cond["risk_flags"] = rf
                signal["conditions"] = cond

            confidence = _confidence_from_analysis(analysis)
            pair_adj = perf_memory.pair_risk_adjustment(pair)
            risk_pct_used = min(RISK_PERCENT, 0.005) if reversal_trade else RISK_PERCENT
            open_window_adj = 0.9 if open_window_active else 1.0
            lot = engine["risk"].calculate_position_size(
                entry=signal["entry"],
                stop_loss=signal["sl"],
                leverage=leverage,
                confidence=confidence,
                risk_multiplier=float(analysis.get("risk_multiplier", 1.0)) * pair_adj * open_window_adj,
                risk_percent_override=risk_pct_used,
            )
            if lot is None:
                return

            meta = {
                "session": session_name,
                "timeframe_analysis": "1W/1D/4H/1H",
                "timeframe_entry": "M1",
                "tp1": signal.get("tp1"),
                "tp2": signal.get("tp2", signal.get("tp")),
                "tp3": signal.get("tp3", signal.get("tp")),
                "risk_pct": risk_pct_used,
                "tier": signal.get("tier", analysis.get("tier")),
                "quality": analysis.get("quality"),
                "confluence_fired": analysis.get("confluence_fired"),
                "confluence_total": analysis.get("confluence_total"),
                "news_impact": news_window.get("impact", "NONE"),
                "fundamental_context": str(news_window.get("status", "CLEAR")),
                "news_events_nearby": "",
                "sentiment": "BULLISH" if str(analysis.get("bias")) == "bullish" else ("BEARISH" if str(analysis.get("bias")) == "bearish" else "NEUTRAL"),
            }
            if reversal_trade:
                meta["fundamental_context"] = f"{meta['fundamental_context']} | REVERSAL_CATCH"
            if open_window_active:
                meta["fundamental_context"] = f"{meta['fundamental_context']} | {open_window_label}"
            event = news_window.get("event") or {}
            if event:
                ev_time = event.get("time", "")
                ev_ccy = event.get("currency", "")
                ev_title = event.get("title", "")
                ev_impact = event.get("impact", news_window.get("impact", ""))
                meta["news_events_nearby"] = f"{ev_time} | {ev_ccy} | {ev_impact} | {ev_title}".strip(" |")
            else:
                upcoming = news.get_upcoming_events(hours=24, pair=pair)
                if upcoming:
                    e = upcoming[0]
                    meta["news_events_nearby"] = (
                        f"{e.get('time', '')} | {e.get('currency', '')} | {e.get('impact', '')} | {e.get('title', '')}"
                    ).strip(" |")
                else:
                    meta["news_events_nearby"] = "NONE (next 24h)"

            trade_id = logger.log(
                pair=pair,
                direction=signal["direction"],
                entry=signal["entry"],
                sl=signal["sl"],
                tp=signal["tp"],
                lot_size=lot,
                rr=signal.get("rr"),
                conditions=signal.get("conditions"),
                meta=meta,
            )
            if trade_id:
                open_trades[pair].append(
                    {
                        "trade_id": trade_id,
                        "direction": signal["direction"],
                        "entry": float(signal["entry"]),
                        "sl": float(signal["sl"]),
                        "tp1": float(signal.get("tp1", signal.get("tp"))),
                        "tp2": float(signal.get("tp2", signal.get("tp"))),
                        "tp3": float(signal.get("tp3", signal.get("tp"))),
                        "lot_size": float(lot),
                        "be_armed": False,
                        "tp1_hit": False,
                        "tp2_hit": False,
                    }
                )
                print(f"[JOURNAL] {pair} {signal['direction']} opened | trade_id={trade_id}")

            last_signal_ts[pair] = now_ts
            last_signal_dir[pair] = direction

        except Exception as exc:
            print(f"[WARN] Tick error ({tick.get('instrument', '?')}): {exc}")

    broker = OandaBroker(OANDA_API_KEY, OANDA_ACCOUNT_ID, environment=OANDA_ENV)
    _reconnect_delay = 5
    try:
        while True:
            try:
                broker.stream_prices(PAIRS, on_tick)
                break  # stream ended cleanly
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[WARN] Stream disconnected ({exc}). Reconnecting in {_reconnect_delay}s...")
                time.sleep(_reconnect_delay)
                _reconnect_delay = min(_reconnect_delay * 2, 120)
    except KeyboardInterrupt:
        print("\nEngine stopped by user.")
    finally:
        mtf.stop()


if __name__ == "__main__":
    main()
