# ======================================================
# position.py — Lot Size & Margin Calculator
# ======================================================

class PositionSizer:
    def __init__(self, user):
        self.user = user

    # --------------------------------------------------
    def pip_value(self, pair, lot_size):
        if "XAU" in pair:
            return lot_size * 1.0
        return lot_size * 10.0  # Forex standard

    # --------------------------------------------------
    def calculate_lot_size(self, pair, entry, sl):
        risk_amount = self.user.risk_amount()
        stop_distance = abs(entry - sl)

        if stop_distance <= 0:
            return None

        if "XAU" in pair or "XAG" in pair:
            # Gold/Silver: pip = 0.01, pip_value = $1 per standard lot per pip
            pip_size = 0.01
            pip_val  = 1.0
        elif "JPY" in pair:
            # JPY pairs: pip = 0.01; pip_value ≈ $10 per standard lot (USD-quoted approx)
            pip_size = 0.01
            pip_val  = 10.0
        else:
            # Standard forex: pip = 0.0001, pip_value = $10 per standard lot
            pip_size = 0.0001
            pip_val  = 10.0

        # Correct formula: risk_amount = stop_pips * pip_value_per_lot * lot_size
        stop_pips = stop_distance / max(pip_size, 1e-8)
        lot = risk_amount / max(stop_pips * pip_val, 1e-9)
        lot = round(lot, 2)

        if lot < self.user.min_lot:
            return None

        if lot > self.user.max_lot:
            lot = self.user.max_lot

        return lot

    # --------------------------------------------------
    def margin_required(self, pair, lot_size, price):
        if "XAU" in pair:
            contract = self.user.gold_contract_size
        else:
            contract = self.user.forex_contract_size

        return (lot_size * contract * price) / self.user.leverage
