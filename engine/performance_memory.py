from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(str(ts).strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _week_key(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class PerformanceMemory:
    def __init__(
        self,
        journal_file: str = os.path.join("output", "trade_journal.csv"),
        output_file: str = os.path.join("output", "performance_memory.json"),
    ):
        self.journal_file = journal_file
        self.output_file = output_file
        self._last_mtime: float = -1.0
        self.summary: Dict = self._empty_summary()

    @staticmethod
    def _empty_summary() -> Dict:
        return {
            "timestamp_utc": "",
            "overall": {
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "win_rate": 0.0,
                "avg_pnl_pips": 0.0,
                "avg_pnl_usd": 0.0,
            },
            "top_pattern_memory": [],
            "session_analysis": {},
            "pair_behavior": {},
            "weekly_audit": {
                "recent_weeks": [],
                "below_50_two_weeks": False,
                "pause_recommended": False,
            },
        }

    def refresh(self, force: bool = False) -> Dict:
        if not os.path.exists(self.journal_file):
            self.summary = self._empty_summary()
            self._persist()
            return self.summary

        try:
            mtime = os.path.getmtime(self.journal_file)
        except Exception:
            mtime = -1.0

        if not force and self._last_mtime >= 0 and mtime == self._last_mtime:
            return self.summary

        self._last_mtime = mtime
        rows = self._read_rows()
        self.summary = self._build_summary(rows)
        self._persist()
        return self.summary

    def get_summary(self) -> Dict:
        return self.summary or self._empty_summary()

    def pause_recommended(self) -> bool:
        audit = self.get_summary().get("weekly_audit", {})
        return bool(audit.get("pause_recommended", False))

    def pair_risk_adjustment(self, pair: str) -> float:
        pair_stats = (self.get_summary().get("pair_behavior") or {}).get(str(pair), {})
        wr = _safe_float(pair_stats.get("win_rate"), 0.0)
        trades = int(_safe_float(pair_stats.get("trades"), 0))
        if trades < 5:
            # Too little data — use standard sizing
            return 1.0
        if trades < 8:
            # Tentative: apply light reduction only on clearly bad early results
            if wr < 40.0:
                return 0.9
            return 1.0
        # Sufficient sample (8+ trades): apply full adjustments
        if wr < 45.0:
            return 0.75
        if wr < 50.0:
            return 0.9
        return 1.0

    def _read_rows(self) -> List[Dict]:
        out: List[Dict] = []
        try:
            with open(self.journal_file, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    status = str(row.get("status", "")).upper()
                    result = str(row.get("result", "")).upper()
                    if status != "CLOSED":
                        continue
                    if result not in ("WIN", "LOSS", "BREAKEVEN"):
                        continue
                    out.append(row)
        except Exception:
            return []
        return out

    def _build_summary(self, rows: List[Dict]) -> Dict:
        summary = self._empty_summary()
        summary["timestamp_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if not rows:
            return summary

        wins = sum(1 for r in rows if str(r.get("result", "")).upper() == "WIN")
        losses = sum(1 for r in rows if str(r.get("result", "")).upper() == "LOSS")
        be = sum(1 for r in rows if str(r.get("result", "")).upper() == "BREAKEVEN")
        closed = len(rows)
        summary["overall"] = {
            "closed_trades": closed,
            "wins": wins,
            "losses": losses,
            "breakeven": be,
            "win_rate": round((wins / closed) * 100.0, 2) if closed else 0.0,
            "avg_pnl_pips": round(sum(_safe_float(r.get("pnl_pips")) for r in rows) / max(closed, 1), 3),
            "avg_pnl_usd": round(sum(_safe_float(r.get("pnl_usd")) for r in rows) / max(closed, 1), 3),
        }

        session_pair = defaultdict(lambda: {"trades": 0, "wins": 0, "pips": 0.0, "usd": 0.0})
        pair_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pips": 0.0, "usd": 0.0})
        pattern_stats = defaultdict(lambda: {"trades": 0, "wins": 0})
        weekly_stats = defaultdict(lambda: {"trades": 0, "wins": 0})

        factor_cols = [
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
            "confluence_fibonacci",
        ]

        for row in rows:
            pair = str(row.get("pair", "")).upper()
            sess = str(row.get("session", "")).strip() or "UNKNOWN"
            result = str(row.get("result", "")).upper()
            is_win = result == "WIN"
            pips = _safe_float(row.get("pnl_pips"))
            usd = _safe_float(row.get("pnl_usd"))

            ps = pair_stats[pair]
            ps["trades"] += 1
            ps["wins"] += 1 if is_win else 0
            ps["pips"] += pips
            ps["usd"] += usd

            sk = f"{pair}|{sess}"
            ss = session_pair[sk]
            ss["trades"] += 1
            ss["wins"] += 1 if is_win else 0
            ss["pips"] += pips
            ss["usd"] += usd

            active = []
            for c in factor_cols:
                v = str(row.get(c, "")).strip().lower()
                if v in ("true", "1", "yes"):
                    active.append(c.replace("confluence_", ""))
            pattern_key = "+".join(sorted(active)) if active else "none"
            pt = pattern_stats[pattern_key]
            pt["trades"] += 1
            pt["wins"] += 1 if is_win else 0

            t_raw = row.get("closed_at_utc") or row.get("date_time") or row.get("opened_at_utc")
            dt = _parse_ts(str(t_raw))
            if dt:
                wk = _week_key(dt)
                ws = weekly_stats[wk]
                ws["trades"] += 1
                ws["wins"] += 1 if is_win else 0

        pair_out = {}
        for p, v in pair_stats.items():
            wr = round((v["wins"] / v["trades"]) * 100.0, 2) if v["trades"] else 0.0
            pair_out[p] = {
                "trades": v["trades"],
                "wins": v["wins"],
                "win_rate": wr,
                "avg_pnl_pips": round(v["pips"] / max(v["trades"], 1), 3),
                "avg_pnl_usd": round(v["usd"] / max(v["trades"], 1), 3),
                "behavior_note": self._pair_behavior_note(p, wr, int(v["trades"])),
            }
        summary["pair_behavior"] = pair_out

        session_out = {}
        for sk, v in session_pair.items():
            pair, sess = sk.split("|", 1)
            session_out.setdefault(pair, {})
            session_out[pair][sess] = {
                "trades": v["trades"],
                "wins": v["wins"],
                "win_rate": round((v["wins"] / v["trades"]) * 100.0, 2) if v["trades"] else 0.0,
                "avg_pnl_pips": round(v["pips"] / max(v["trades"], 1), 3),
                "avg_pnl_usd": round(v["usd"] / max(v["trades"], 1), 3),
            }
        summary["session_analysis"] = session_out

        pattern_ranked = []
        for k, v in pattern_stats.items():
            trades = int(v["trades"])
            wins = int(v["wins"])
            wr = round((wins / trades) * 100.0, 2) if trades else 0.0
            pattern_ranked.append(
                {
                    "pattern_combo": k,
                    "trades": trades,
                    "wins": wins,
                    "win_rate": wr,
                }
            )
        pattern_ranked.sort(key=lambda x: (x["win_rate"], x["trades"]), reverse=True)
        summary["top_pattern_memory"] = pattern_ranked[:10]

        weeks = []
        for w, v in weekly_stats.items():
            trades = int(v["trades"])
            wins = int(v["wins"])
            weeks.append(
                {
                    "week": w,
                    "trades": trades,
                    "wins": wins,
                    "win_rate": round((wins / trades) * 100.0, 2) if trades else 0.0,
                }
            )
        weeks.sort(key=lambda x: x["week"])
        recent = weeks[-6:]
        last_two = recent[-2:]
        below_two = len(last_two) == 2 and all((w["trades"] >= 3 and w["win_rate"] < 50.0) for w in last_two)
        summary["weekly_audit"] = {
            "recent_weeks": recent,
            "below_50_two_weeks": below_two,
            "pause_recommended": below_two,
        }
        return summary

    @staticmethod
    def _pair_behavior_note(pair: str, win_rate: float, trades: int) -> str:
        p = str(pair or "").upper()
        if "EUR_USD" in p:
            base = "EUR/USD usually respects major S/R and structured retests."
        elif "GBP" in p:
            base = "GBP pairs are often more volatile with sharper sweeps."
        elif "JPY" in p:
            base = "JPY pairs can react aggressively to sentiment and risk-on/off flows."
        else:
            base = "Track this pair with strict zone discipline and session context."

        if trades < 8:
            perf = "Sample size still small; keep standard risk until more data."
        elif win_rate < 45.0:
            perf = "Recent behavior is unstable; reduce size and require stronger confluence."
        elif win_rate < 55.0:
            perf = "Mixed behavior; prefer London/NY and avoid marginal setups."
        else:
            perf = "Current behavior is supportive; continue with disciplined execution."
        return f"{base} {perf}"

    def _persist(self) -> None:
        os.makedirs(os.path.dirname(self.output_file) or ".", exist_ok=True)
        tmp = f"{self.output_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.summary, f)
        os.replace(tmp, self.output_file)
