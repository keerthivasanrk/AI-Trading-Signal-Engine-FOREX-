from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from config.settings import MAX_CONSECUTIVE_LOSSES_PER_DAY

_RISK_STATE_FILE = os.path.join("output", "risk_state.json")


class RiskEngine:
    def __init__(self, balance, risk_percent=0.01):
        self.balance = float(balance)
        self.risk_percent = float(risk_percent)
        self.peak_balance = float(balance)
        self.current_drawdown_pct = 0.0
        self.consecutive_losses = 0
        self.daily_loss_hits = 0
        self._loss_day = datetime.now(timezone.utc).date()
        self._load_state()

    def _load_state(self) -> None:
        """Restore daily loss counter and drawdown state from disk on startup."""
        try:
            if not os.path.exists(_RISK_STATE_FILE):
                return
            with open(_RISK_STATE_FILE, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            saved_day = state.get("loss_day", "")
            today = datetime.now(timezone.utc).date().isoformat()
            if saved_day == today:
                self.consecutive_losses = int(state.get("consecutive_losses", 0))
                self.daily_loss_hits = int(state.get("daily_loss_hits", 0))
                saved_balance = float(state.get("balance", self.balance))
                saved_peak = float(state.get("peak_balance", self.peak_balance))
                # Only restore simulated balance if it differs from user-entered amount
                # by less than 50% (guards against stale state from a different session).
                if abs(saved_balance - self.balance) / max(self.balance, 1.0) < 0.50:
                    self.balance = saved_balance
                    self.peak_balance = max(self.peak_balance, saved_peak)
                    if self.peak_balance > 0:
                        self.current_drawdown_pct = max(
                            0.0,
                            (self.peak_balance - self.balance) / self.peak_balance * 100.0,
                        )
        except Exception:
            pass  # State file corrupt or missing — start fresh

    def _save_state(self) -> None:
        """Persist daily loss counter and drawdown state to disk."""
        try:
            os.makedirs(os.path.dirname(_RISK_STATE_FILE) or ".", exist_ok=True)
            state = {
                "loss_day": self._loss_day.isoformat(),
                "consecutive_losses": self.consecutive_losses,
                "daily_loss_hits": self.daily_loss_hits,
                "balance": round(self.balance, 4),
                "peak_balance": round(self.peak_balance, 4),
                "current_drawdown_pct": round(self.current_drawdown_pct, 4),
            }
            tmp = _RISK_STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp, _RISK_STATE_FILE)
        except Exception:
            pass

    def _ensure_day_rollover(self):
        today = datetime.now(timezone.utc).date()
        if today != self._loss_day:
            self._loss_day = today
            self.consecutive_losses = 0
            self.daily_loss_hits = 0
            self._save_state()

    def _drawdown_factor(self) -> float:
        if self.current_drawdown_pct >= 20.0:
            return 0.0
        if self.current_drawdown_pct >= 10.0:
            return 0.5
        return 1.0

    def can_trade_today(self):
        self._ensure_day_rollover()
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES_PER_DAY:
            return False, f"Daily stop: {self.consecutive_losses} consecutive losses"
        if self._drawdown_factor() <= 0.0:
            return False, "Account drawdown >= 20%: trading halted"
        return True, "Risk checks passed"

    def calculate_position_size(
        self,
        entry,
        stop_loss,
        leverage=1,
        confidence=1.0,
        risk_multiplier=1.0,
        risk_percent_override=None,
    ):
        if entry is None or stop_loss is None:
            return None

        stop_distance = abs(float(entry) - float(stop_loss))
        if stop_distance <= 0:
            return None

        can_trade, _ = self.can_trade_today()
        if not can_trade:
            return None

        conf = max(0.5, min(1.25, float(confidence)))
        rm = max(0.25, min(1.0, float(risk_multiplier)))
        dd = self._drawdown_factor()
        base_risk_pct = self.risk_percent if risk_percent_override is None else float(risk_percent_override)
        base_risk_pct = max(0.001, min(0.05, base_risk_pct))
        effective_risk = base_risk_pct * conf * rm * dd
        risk_amount = self.balance * effective_risk
        if risk_amount <= 0:
            return None

        position_size = (risk_amount / stop_distance) * float(leverage)
        return round(position_size, 2)

    def register_trade_result(self, pnl_amount):
        self._ensure_day_rollover()
        pnl = float(pnl_amount)
        self.balance += pnl
        self.peak_balance = max(self.peak_balance, self.balance)
        if self.peak_balance > 0:
            self.current_drawdown_pct = max(0.0, (self.peak_balance - self.balance) / self.peak_balance * 100.0)
        if pnl < 0:
            self.consecutive_losses += 1
            self.daily_loss_hits += 1
        else:
            self.consecutive_losses = 0
        self._save_state()
