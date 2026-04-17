from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from typing import Dict, List, Optional, Tuple

import feedparser

from config.settings import NEWS_BUFFER_MINUTES, NEWS_HARD_BLOCK_HOURS


FF_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

FALLBACK_WINDOWS = [
    (dtime(8, 25), dtime(8, 35)),
    (dtime(12, 25), dtime(13, 5)),
    (dtime(14, 55), dtime(15, 5)),
    (dtime(17, 55), dtime(18, 5)),
]


class NewsEngine:
    def __init__(self, buffer_minutes: int = NEWS_BUFFER_MINUTES, currencies: Optional[List[str]] = None):
        self.buffer = timedelta(minutes=buffer_minutes)
        self.watched = set(currencies or ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "XAU", "XAG"])
        self.events: List[Tuple[datetime, str, str, str]] = []
        self._use_feed = True

    def fetch_news(self) -> None:
        try:
            feed = feedparser.parse(FF_FEED_URL)
            if feed.bozo and not feed.entries:
                raise ValueError("feed parse error")

            parsed: List[Tuple[datetime, str, str, str]] = []
            for entry in feed.entries:
                impact = str(entry.get("ff_impact", "")).strip().upper()
                if impact not in ("HIGH", "MEDIUM", "LOW"):
                    continue
                currency = str(entry.get("ff_currency", "")).strip().upper()
                title = str(entry.get("title", "")).strip()
                date_str = str(entry.get("ff_date", "")).strip()
                time_str = str(entry.get("ff_time", "")).strip()
                if not date_str:
                    continue
                dt = self._parse_ff_datetime(date_str, time_str)
                if dt is None:
                    continue
                parsed.append((dt, currency, impact, title))

            self.events = parsed
            self._use_feed = True
            print(f"   NewsEngine: loaded {len(self.events)} events from ForexFactory")
        except Exception as exc:
            # Preserve the last successfully fetched events so upcoming high-impact
            # events are still respected even during a temporary feed outage.
            # Only switch to generic fallback windows if we have no event data at all.
            if not self.events:
                self._use_feed = False
            print(
                f"   NewsEngine: RSS unavailable ({exc}). "
                f"{'Retaining ' + str(len(self.events)) + ' cached events.' if self.events else 'Using fallback windows.'}"
            )

    def _events_for_pair(self, pair: Optional[str]) -> List[Tuple[datetime, str, str, str]]:
        watched = self._currencies_for_pair(pair)
        if not watched:
            return list(self.events)
        return [ev for ev in self.events if ev[1] in watched]

    def evaluate_trade_window(self, pair: Optional[str] = None) -> Dict:
        now = datetime.now(timezone.utc)

        if self._use_feed and self.events:
            nearest = None
            for event_dt, currency, impact, title in self._events_for_pair(pair):
                sec = int((event_dt - now).total_seconds())
                if sec < -int(self.buffer.total_seconds()):
                    continue
                if sec > NEWS_HARD_BLOCK_HOURS * 3600:
                    continue
                if nearest is None or abs(sec) < abs(nearest["seconds_away"]):
                    nearest = {
                        "time": event_dt.isoformat(),
                        "currency": currency,
                        "impact": impact,
                        "title": title,
                        "seconds_away": sec,
                    }

            if nearest is None:
                return {
                    "block": False,
                    "impact": "NONE",
                    "risk_multiplier": 1.0,
                    "status": "CLEAR",
                    "event": None,
                }

            impact = nearest["impact"]
            if impact == "HIGH":
                return {
                    "block": True,
                    "impact": "HIGH",
                    "risk_multiplier": 0.0,
                    "status": f"BLOCKED: HIGH impact ({nearest['currency']})",
                    "event": nearest,
                }
            if impact == "MEDIUM":
                return {
                    "block": False,
                    "impact": "MEDIUM",
                    "risk_multiplier": 0.5,
                    "status": f"CAUTION: MEDIUM impact ({nearest['currency']})",
                    "event": nearest,
                }
            return {
                "block": False,
                "impact": "LOW",
                "risk_multiplier": 0.8,
                "status": f"LOW impact ({nearest['currency']})",
                "event": nearest,
            }

        now_t = now.time()
        for start, end in FALLBACK_WINDOWS:
            if start <= now_t < end:
                return {
                    "block": True,
                    "impact": "HIGH",
                    "risk_multiplier": 0.0,
                    "status": "Fallback HIGH-risk window",
                    "event": None,
                }
        return {
            "block": False,
            "impact": "NONE",
            "risk_multiplier": 1.0,
            "status": "CLEAR",
            "event": None,
        }

    def is_high_impact(self, pair: Optional[str] = None) -> bool:
        return bool(self.evaluate_trade_window(pair).get("block", False))

    # Backward compatibility for controller.py.
    def is_high_risk_time(self, pair: Optional[str] = None):
        result = self.evaluate_trade_window(pair)
        return result.get("block", False), result.get("status", "")

    def get_upcoming_events(self, hours: int = 24, pair: Optional[str] = None) -> List[Dict]:
        now = datetime.now(timezone.utc)
        future = now + timedelta(hours=hours)
        out: List[Dict] = []
        if self._use_feed and self.events:
            for event_dt, currency, impact, title in self._events_for_pair(pair):
                if now <= event_dt <= future:
                    out.append(
                        {
                            "time": event_dt.isoformat(),
                            "currency": currency,
                            "impact": impact,
                            "title": title,
                            "seconds_away": int((event_dt - now).total_seconds()),
                        }
                    )
        out.sort(key=lambda x: x["seconds_away"])
        return out

    @staticmethod
    def _currencies_for_pair(pair: Optional[str]) -> set:
        if not pair:
            return set()
        p = pair.upper().replace("/", "_")
        if "_" not in p:
            return {p}
        base, quote = p.split("_", 1)
        return {base, quote}

    @staticmethod
    def _parse_ff_datetime(date_str: str, time_str: str) -> Optional[datetime]:
        dt_str = f"{date_str} {time_str}".strip()
        for fmt in ("%B %d, %Y %I:%M%p", "%b %d, %Y %I:%M%p", "%B %d, %Y", "%b %d, %Y"):
            try:
                dt = datetime.strptime(dt_str, fmt)
                # ForexFactory publishes event times in US Eastern Time (ET), not UTC.
                # Convert to UTC: EDT (UTC-4) roughly March–October, EST (UTC-5) November–February.
                try:
                    import pytz  # type: ignore
                    et = pytz.timezone("US/Eastern")
                    dt_utc = et.localize(dt).astimezone(timezone.utc).replace(tzinfo=timezone.utc)
                except Exception:
                    # pytz unavailable — use simplified month-based DST offset.
                    utc_offset = timedelta(hours=4) if (3 <= dt.month <= 10) else timedelta(hours=5)
                    dt_utc = (dt + utc_offset).replace(tzinfo=timezone.utc)
                return dt_utc
            except ValueError:
                continue
        return None

