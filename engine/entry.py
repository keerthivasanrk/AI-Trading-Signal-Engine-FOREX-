from config.settings import MIN_RR_RATIO, RR_RATIO


class EntryEngine:
    """Signal gate that supports both legacy and APEX confluence analysis."""

    def check(
        self,
        candle,
        structure,
        liquidity,
        trend="neutral",
        volume_ok=False,
        mtf_bias="neutral",
        analysis=None,
    ):
        # APEX confluence-driven path.
        if analysis is not None:
            if analysis.get("state") != "SIGNAL":
                return None
            plan = analysis.get("trade_plan", {})
            rr = float(plan.get("rr", 0.0) or 0.0)
            if rr < MIN_RR_RATIO:
                return None
            direction = plan.get("direction", "WAIT")
            if direction not in ("BUY", "SELL"):
                return None
            return {
                "direction": direction,
                "entry": round(float(plan.get("entry", plan.get("entry", 0.0))), 6),
                "sl": round(float(plan.get("sl", 0.0)), 6),
                "tp": round(float(plan.get("tp2", plan.get("tp1", plan.get("tp", 0.0)))), 6),
                "tp1": round(float(plan.get("tp1", 0.0)), 6),
                "tp2": round(float(plan.get("tp2", plan.get("tp1", 0.0))), 6),
                "tp3": round(float(plan.get("tp3", plan.get("tp2", plan.get("tp1", 0.0)))), 6),
                "rr": round(rr, 3),
                "tier": analysis.get("tier"),
                "quality": analysis.get("quality"),
                "confluence_fired": analysis.get("confluence_fired"),
                "confluence_total": analysis.get("confluence_total"),
                "conditions": {
                    "state": analysis.get("state"),
                    "bias": analysis.get("bias"),
                    "checklist": analysis.get("checklist"),
                    "risk_flags": analysis.get("risk_flags"),
                },
            }

        # Legacy path (kept for compatibility).
        if mtf_bias not in ("bullish", "bearish"):
            return None

        bos = structure.get("bos")
        choch = structure.get("choch")
        if not bos and not choch:
            return None

        structural_dir = bos or choch
        if structural_dir != mtf_bias:
            return None

        if mtf_bias == "bullish":
            swept = liquidity.get("sell_side_sweep", False)
        else:
            swept = liquidity.get("buy_side_sweep", False)
        if not swept:
            return None
        if trend != mtf_bias or not volume_ok:
            return None

        entry = float(candle["close"])
        if mtf_bias == "bullish":
            sl = float(candle["low"])
            risk = entry - sl
            if risk <= 0:
                return None
            tp = entry + risk * RR_RATIO
            direction = "BUY"
        else:
            sl = float(candle["high"])
            risk = sl - entry
            if risk <= 0:
                return None
            tp = entry - risk * RR_RATIO
            direction = "SELL"

        rr = abs(tp - entry) / risk if risk > 0 else 0.0
        if rr < MIN_RR_RATIO:
            return None

        return {
            "direction": direction,
            "entry": round(entry, 6),
            "sl": round(sl, 6),
            "tp": round(tp, 6),
            "rr": round(rr, 3),
            "conditions": {
                "mtf_bias": mtf_bias,
                "structure": structural_dir,
                "liquidity": "sell_sweep" if mtf_bias == "bullish" else "buy_sweep",
                "trend": trend,
                "volume_ok": volume_ok,
            },
        }
