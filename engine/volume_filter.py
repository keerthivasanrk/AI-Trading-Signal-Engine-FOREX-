class VolumeFilter:
    def __init__(self, lookback=20, multiplier=1.2):
        self.lookback    = lookback
        self.multiplier  = multiplier

    @staticmethod
    def _avg(values, default=0.0):
        vals = [float(v) for v in values if v is not None]
        return (sum(vals) / len(vals)) if vals else float(default)

    def is_volume_confirmed(self, candle, history):
        if not history or len(history) < 3:
            return True

        recent   = history[-self.lookback:] if len(history) >= self.lookback else history
        avg_vol  = sum(c.get("volume", 1) for c in recent) / len(recent)

        if avg_vol <= 0:
            return True

        current_vol = candle.get("volume", 1)
        return current_vol >= (avg_vol * self.multiplier)

    def volume_ratio(self, candle, history):
        if not history:
            return 1.0

        recent  = history[-self.lookback:] if len(history) >= self.lookback else history
        avg_vol = sum(c.get("volume", 1) for c in recent) / len(recent)

        if avg_vol <= 0:
            return 1.0

        return round(candle.get("volume", 1) / avg_vol, 2)

    def institutional_context(self, candle, history, pair=None):
        """
        Wyckoff + volume-fight style context:
        - Price down + volume up  -> institutional buying pressure (bullish)
        - Price up + volume down  -> weak rally/distribution (bearish)
        - Accumulation/distribution zones from consolidation + location + volume rise
        """
        bars = list(history or [])
        if candle:
            bars = bars + [candle]

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

        if len(bars) < 8:
            return out

        closes = [float(c.get("close", 0.0)) for c in bars]
        highs = [float(c.get("high", 0.0)) for c in bars]
        lows = [float(c.get("low", 0.0)) for c in bars]
        vols = [float(c.get("volume", 1.0) or 1.0) for c in bars]

        look = min(max(8, self.lookback), len(bars) - 1)
        price_delta = closes[-1] - closes[-look]
        # Use a tiny adaptive threshold so micro-noise is not treated as trend.
        px_threshold = max(abs(closes[-1]) * 0.00005, 1e-8)
        if price_delta > px_threshold:
            out["price_trend"] = "up"
        elif price_delta < -px_threshold:
            out["price_trend"] = "down"

        recent_n = min(5, len(vols))
        prev_n = min(max(3, look - recent_n), len(vols) - recent_n)
        recent_vol = self._avg(vols[-recent_n:], 1.0)
        prev_vol = self._avg(vols[-(recent_n + prev_n):-recent_n], 1.0)

        if recent_vol > prev_vol * 1.10:
            out["volume_trend"] = "up"
        elif recent_vol < prev_vol * 0.90:
            out["volume_trend"] = "down"

        out["down_price_up_volume_bullish"] = out["price_trend"] == "down" and out["volume_trend"] == "up"
        out["up_price_down_volume_bearish"] = out["price_trend"] == "up" and out["volume_trend"] == "down"

        zone_look = min(12, len(bars))
        zone_high = max(highs[-zone_look:])
        zone_low = min(lows[-zone_look:])
        zone_range = max(zone_high - zone_low, 1e-9)
        mean_close = self._avg(closes[-zone_look:], closes[-1])
        consolidation = (zone_range / max(abs(mean_close), 1e-9)) <= 0.0035

        near_low = closes[-1] <= (zone_low + zone_range * 0.35)
        near_high = closes[-1] >= (zone_high - zone_range * 0.35)

        out["accumulation"] = consolidation and near_low and (out["volume_trend"] == "up")
        out["distribution"] = consolidation and near_high and (out["volume_trend"] == "up")

        out["green_triangle_bottom"] = bool(out["down_price_up_volume_bullish"] or out["accumulation"])
        out["red_triangle_top"] = bool(out["up_price_down_volume_bearish"] or out["distribution"])

        last = bars[-1]
        o = float(last.get("open", closes[-1]))
        h = float(last.get("high", closes[-1]))
        l = float(last.get("low", closes[-1]))
        c = float(last.get("close", closes[-1]))
        body = abs(c - o)
        rng = max(h - l, 1e-9)
        out["partial_institutional_candle"] = body <= rng * 0.35

        return out
