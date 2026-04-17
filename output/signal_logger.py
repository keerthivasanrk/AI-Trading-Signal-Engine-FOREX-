import csv
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional


_LOG_DIR = os.path.join(os.path.dirname(__file__))
_LOG_FILE = os.path.join(_LOG_DIR, "signals.log")
_JOURNAL_FILE = os.path.join(_LOG_DIR, "trade_journal.csv")

_HEADER = ["timestamp", "pair", "direction", "entry", "sl", "tp", "lot_size", "rr"]
_JOURNAL_HEADER = [
    "trade_id",
    "date_time",
    "session",
    "pair",
    "direction",
    "timeframe_analysis",
    "timeframe_entry",
    "confluence_sr_zone",
    "confluence_supply_demand_zone",
    "confluence_rsi_level_divergence",
    "confluence_candlestick_pattern",
    "confluence_ema_sma_trend",
    "confluence_donchian_channel",
    "confluence_volume_signal",
    "confluence_arty_signal",
    "confluence_macd_adx",
    "confluence_smc",
    "confluence_parabolic_sar",
    "confluence_chart_pattern",
    "confluence_fundamental_news",
    "total_confluences_13",
    "confluence_fibonacci",
    "entry_price",
    "stop_loss",
    "target_1_1to1",
    "target_2_1to2",
    "target_3_1to3",
    "risk_pct_of_account",
    "rr_ratio",
    "fundamental_context",
    "news_events_nearby",
    "sentiment",
    "result",
    "pnl_pips",
    "pnl_usd",
    "post_trade_what_went_right",
    "post_trade_what_went_wrong",
    "post_trade_take_trade_again",
    "post_trade_emotion_psychology",
    "status",
    "opened_at_utc",
    "closed_at_utc",
    "risk_flags_json",
    "checklist_json",
]


def _utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _to_bool(v) -> bool:
    return bool(v)


def _pct_text(v) -> str:
    try:
        fv = float(v)
        if fv <= 1.0:
            return f"{fv * 100:.2f}%"
        return f"{fv:.2f}%"
    except Exception:
        return str(v or "")


def _checklist_13(checklist: Dict) -> Dict[str, bool]:
    c = checklist or {}
    return {
        "sr_zone": _to_bool(c.get("sr_zone_aligned")),
        "supply_demand_zone": _to_bool(c.get("supply_demand_zone_aligned")),
        "rsi_level_divergence": _to_bool(c.get("rsi_level_direction_aligned")) and _to_bool(c.get("rsi_divergence_aligned")),
        "candlestick_pattern": _to_bool(c.get("candlestick_pattern_aligned")),
        "ema_sma_trend": _to_bool(c.get("ema8_sma18_aligned")),
        "donchian_channel": _to_bool(c.get("donchian_touch_aligned")),
        "volume_signal": _to_bool(c.get("volume_institutional_aligned")),
        "arty_signal": _to_bool(c.get("arty_signal_aligned")),
        "macd_adx": _to_bool(c.get("macd_signal_aligned")) and _to_bool(c.get("adx_strength_aligned")),
        "smc": _to_bool(c.get("smc_signal_aligned")),
        "parabolic_sar": _to_bool(c.get("parabolic_sar_aligned")),
        "chart_pattern": _to_bool(c.get("chart_pattern_confirmation")),
        "fundamental_news": _to_bool(c.get("fundamental_news_clear")),
    }


class SignalLogger:
    def __init__(self, log_file=_LOG_FILE, journal_file=_JOURNAL_FILE):
        self.log_file = log_file
        self.journal_file = journal_file
        self._ensure_files()

    def log(self, pair, direction, entry, sl, tp, lot_size, rr=None, conditions=None, meta=None):
        now_str = _utc_now_str()
        rr_display = f"{float(rr):.2f}" if rr is not None else "N/A"
        trade_id = f"{int(datetime.now(timezone.utc).timestamp() * 1000)}_{pair}_{direction}"

        print(
            f"[SIGNAL] {now_str} | {pair} | {direction} | "
            f"entry={entry} sl={sl} tp={tp} lot={lot_size} rr={rr_display}"
        )

        with open(self.log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([now_str, pair, direction, entry, sl, tp, lot_size, rr_display])

        self._append_journal(
            trade_id=trade_id,
            ts=now_str,
            pair=pair,
            direction=direction,
            entry=entry,
            sl=sl,
            tp=tp,
            rr_display=rr_display,
            conditions=conditions or {},
            meta=meta or {},
        )
        return trade_id

    def update_trade_result(
        self,
        trade_id: str,
        result: str,
        pnl_pips: Optional[float] = None,
        pnl_usd: Optional[float] = None,
        what_right: str = "",
        what_wrong: str = "",
        take_again: str = "",
        emotion_note: str = "",
    ) -> bool:
        if not trade_id or not os.path.exists(self.journal_file):
            return False

        try:
            with open(self.journal_file, "r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            return False

        updated = False
        for row in rows:
            if str(row.get("trade_id", "")).strip() != str(trade_id).strip():
                continue
            row["result"] = str(result or "").upper()
            row["pnl_pips"] = "" if pnl_pips is None else f"{float(pnl_pips):.2f}"
            row["pnl_usd"] = "" if pnl_usd is None else f"{float(pnl_usd):.2f}"
            row["post_trade_what_went_right"] = str(what_right or row.get("post_trade_what_went_right", ""))
            row["post_trade_what_went_wrong"] = str(what_wrong or row.get("post_trade_what_went_wrong", ""))
            row["post_trade_take_trade_again"] = str(take_again or row.get("post_trade_take_trade_again", ""))
            row["post_trade_emotion_psychology"] = str(emotion_note or row.get("post_trade_emotion_psychology", ""))
            row["status"] = "CLOSED"
            row["closed_at_utc"] = _utc_now_str()
            updated = True
            break

        if not updated:
            return False

        try:
            tmp = self.journal_file + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_JOURNAL_HEADER)
                writer.writeheader()
                for row in rows:
                    writer.writerow({k: row.get(k, "") for k in _JOURNAL_HEADER})
            os.replace(tmp, self.journal_file)
            return True
        except Exception:
            return False

    def _append_journal(self, trade_id, ts, pair, direction, entry, sl, tp, rr_display, conditions, meta):
        checklist = conditions.get("checklist", {}) or {}
        risk_flags = conditions.get("risk_flags", []) or []
        if not isinstance(risk_flags, list):
            risk_flags = [str(risk_flags)]

        con13 = _checklist_13(checklist)
        total13 = sum(1 for v in con13.values() if v)
        con_fib = _to_bool(checklist.get("fibonacci_confluence_aligned"))

        row = {
            "trade_id": trade_id,
            "date_time": ts,
            "session": str(meta.get("session", "")),
            "pair": pair,
            "direction": direction,
            "timeframe_analysis": str(meta.get("timeframe_analysis", "1W/1D/4H/1H")),
            "timeframe_entry": str(meta.get("timeframe_entry", "M1")),
            "confluence_sr_zone": str(con13["sr_zone"]),
            "confluence_supply_demand_zone": str(con13["supply_demand_zone"]),
            "confluence_rsi_level_divergence": str(con13["rsi_level_divergence"]),
            "confluence_candlestick_pattern": str(con13["candlestick_pattern"]),
            "confluence_ema_sma_trend": str(con13["ema_sma_trend"]),
            "confluence_donchian_channel": str(con13["donchian_channel"]),
            "confluence_volume_signal": str(con13["volume_signal"]),
            "confluence_arty_signal": str(con13["arty_signal"]),
            "confluence_macd_adx": str(con13["macd_adx"]),
            "confluence_smc": str(con13["smc"]),
            "confluence_parabolic_sar": str(con13["parabolic_sar"]),
            "confluence_chart_pattern": str(con13["chart_pattern"]),
            "confluence_fundamental_news": str(con13["fundamental_news"]),
            "total_confluences_13": str(total13),
            "confluence_fibonacci": str(con_fib),
            "entry_price": entry,
            "stop_loss": sl,
            "target_1_1to1": meta.get("tp1") or tp,
            "target_2_1to2": meta.get("tp2") or tp,
            "target_3_1to3": meta.get("tp3") or tp,
            "risk_pct_of_account": _pct_text(meta.get("risk_pct", "")),
            "rr_ratio": rr_display,
            "fundamental_context": str(meta.get("fundamental_context", "")),
            "news_events_nearby": str(meta.get("news_events_nearby", "")),
            "sentiment": str(meta.get("sentiment", "")),
            "result": "OPEN",
            "pnl_pips": "",
            "pnl_usd": "",
            "post_trade_what_went_right": "",
            "post_trade_what_went_wrong": "",
            "post_trade_take_trade_again": "",
            "post_trade_emotion_psychology": "",
            "status": "OPEN",
            "opened_at_utc": ts,
            "closed_at_utc": "",
            "risk_flags_json": json.dumps(risk_flags, separators=(",", ":"), ensure_ascii=True),
            "checklist_json": json.dumps(checklist, separators=(",", ":"), ensure_ascii=True),
        }

        with open(self.journal_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_JOURNAL_HEADER)
            writer.writerow(row)

    def _ensure_files(self):
        os.makedirs(os.path.dirname(self.log_file) or ".", exist_ok=True)
        self._ensure_header(self.log_file, _HEADER)
        self._ensure_header(self.journal_file, _JOURNAL_HEADER)

    @staticmethod
    def _ensure_header(path: str, header: List[str]) -> None:
        if not os.path.exists(path):
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(header)
            return

        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                first = next(reader, [])
        except Exception:
            first = []

        if first == header:
            return

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup = f"{path}.bak_{stamp}"
        try:
            os.replace(path, backup)
        except Exception:
            pass
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header)
