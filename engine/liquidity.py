class LiquidityEngine:
    def __init__(self, max_levels=20):
        self.highs = []
        self.lows = []
        self.max_levels = max_levels

    def update(self, candle, structure):
        result = {
            "buy_side_sweep": False,
            "sell_side_sweep": False,
            "buy_side_stop_hunt": False,
            "sell_side_stop_hunt": False,
            "liquidity_grab": False,
            "swept_buy_level": None,
            "swept_sell_level": None,
        }

        if not candle or not structure:
            return result

        high = float(candle.get("high", 0.0))
        low = float(candle.get("low", 0.0))
        close = float(candle.get("close", 0.0))

        if structure.get("swing_high"):
            sh_price = structure.get("swing_high_price")
            if sh_price is not None:
                self.highs.append(float(sh_price))

        if structure.get("swing_low"):
            sl_price = structure.get("swing_low_price")
            if sl_price is not None:
                self.lows.append(float(sl_price))

        self.highs = self.highs[-self.max_levels:]
        self.lows = self.lows[-self.max_levels:]

        if len(self.highs) > 1:
            prev_high = max(self.highs[:-1])
            if high > prev_high:
                result["buy_side_sweep"] = True
                result["swept_buy_level"] = prev_high
                # Classic buy-side stop hunt: wick takes highs, candle closes back below.
                if close < prev_high:
                    result["buy_side_stop_hunt"] = True

        if len(self.lows) > 1:
            prev_low = min(self.lows[:-1])
            if low < prev_low:
                result["sell_side_sweep"] = True
                result["swept_sell_level"] = prev_low
                # Classic sell-side stop hunt: wick takes lows, candle closes back above.
                if close > prev_low:
                    result["sell_side_stop_hunt"] = True

        result["liquidity_grab"] = bool(result["buy_side_stop_hunt"] or result["sell_side_stop_hunt"])

        return result
