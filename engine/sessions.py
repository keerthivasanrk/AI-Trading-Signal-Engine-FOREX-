from datetime import datetime, time, timezone

from config.settings import (
    DECEMBER_BLACKOUT_START_DAY,
    FRIDAY_CUTOFF_UTC_HOUR,
    JANUARY_BLACKOUT_END_DAY,
)


class SessionEngine:
    def __init__(self, timezone_name="UTC"):
        self.timezone_name = timezone_name

        self.sessions = {
            "LONDON": (time(7, 0), time(12, 0)),
            "NEW_YORK": (time(13, 0), time(17, 0)),
            "OVERLAP": (time(12, 0), time(15, 0)),
            "ASIA": (time(0, 0), time(7, 0)),
        }

        self.kill_zones = {
            "LONDON_KZ": (time(7, 0), time(9, 0)),
            "NEW_YORK_KZ": (time(12, 0), time(14, 0)),
        }
        self.bank_holidays = {
            # Major recurring bank-holiday anchors.
            (1, 1),   # New Year
            (12, 25), # Christmas
            (12, 26), # Boxing Day
        }

    def _now_utc(self):
        return datetime.now(timezone.utc)

    def _is_blackout_window(self):
        now = self._now_utc()

        # Weekend / bank-holiday proxy.
        if now.weekday() >= 5:
            return True, "Weekend / holiday liquidity risk"
        if (now.month, now.day) in self.bank_holidays:
            return True, "Major bank holiday"

        # Friday after 14:00 UTC.
        if now.weekday() == 4 and now.time() >= time(FRIDAY_CUTOFF_UTC_HOUR, 0):
            return True, "Friday afternoon cutoff"

        # Dec 16 -> Jan 15 blackout.
        if (now.month == 12 and now.day >= DECEMBER_BLACKOUT_START_DAY) or (
            now.month == 1 and now.day <= JANUARY_BLACKOUT_END_DAY
        ):
            return True, "Seasonal low-liquidity blackout window"

        return False, ""

    def current_session(self):
        now = self._now_utc().time()

        # Check overlap first.
        for session in ("OVERLAP", "LONDON", "NEW_YORK", "ASIA"):
            start, end = self.sessions[session]
            if start <= now < end:
                return session

        return "OFF_SESSION"

    def is_killzone(self):
        now = self._now_utc().time()

        for kz, (start, end) in self.kill_zones.items():
            if start <= now < end:
                return True, kz

        return False, None

    def can_trade_now(self):
        blackout, reason = self._is_blackout_window()
        if blackout:
            return False, reason

        session = self.current_session()
        if session not in ("LONDON", "NEW_YORK", "OVERLAP"):
            return False, f"Session {session} not in allowed windows"

        return True, f"Session open: {session}"
