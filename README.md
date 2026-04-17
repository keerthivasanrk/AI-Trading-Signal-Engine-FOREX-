# AI Trading Signal Engine (FOREX) v3.0

An advanced, professional-grade algorithmic trading signal engine meticulously designed for the FOREX markets (compatible with OANDA). This system processes real-time M1 (1-minute) price ticks into actionable institutional-style structural signals. It seamlessly integrates a continuous multi-engine analytical loop, leveraging market structure mapping, smart money concepts (SMC), algorithmic risk management, fundamental news filtering, and multi-timeframe confluence.

## System Architecture

The core philosophy of this trading engine is **algorithmic institutional precision**. Rather than relying purely on classical retail indicators (like MACD or Stochastics), the engine models price action and structural mechanics, supplemented by risk heuristics and statistical logic.

### Modularity
The platform is heavily modularized, cleanly splitting different forms of analysis into independent engine blocks that collaborate on a per-tick basis:

- **StructureEngine (`engine/structure.py`)**: Detects high-level Market Structure Shifts (MSS / CHoCH) and Break of Structure (BOS) continuously on the sub-minute timeframe.
- **LiquidityEngine (`engine/liquidity.py`)**: Maps out and identifies sweeps of Buy-Side Liquidity (BSL) and Sell-Side Liquidity (SSL), helping validate potential false breakouts (stop hunts).
- **MTFBiasEngine (`engine/mtf_bias.py`)**: Continuously monitors the 1W, 1D, 4H, and 1H trends to ensure entries are aligned with the macroeconomic directional bias.
- **CandleBuilder (`engine/candles.py`)**: Assembles incoming raw pricing tick streams into reliable M1 OHLCV candle structures. 
- **VolumeFilter & TrendFilter (`engine/volume_filter.py`, `engine/trend_filter.py`)**: Confirm institutional footprint scaling and relative momentum to validate moving averages and volume bursts.
- **NewsEngine (`engine/news.py`)**: Tracks a real-time ForexFactory-style economic calendar, maintaining a strict gate to prevent setups directly before, during, or after high-impact fundamental events.
- **SessionEngine (`engine/sessions.py`)**: Identifies optimal global liquid trading periods (London, New York) and prevents signals during historically low-volume periods (like the Asian dead-zone) or high-spread rollover periods.
- **RiskEngine (`engine/risk.py`)**: Manages dynamic position sizing based on stop-loss distance, real-time PIP values, confidence tiering, and equity curve protection (daily drawdown limits, etc.).
- **SetupDetector & EntryEngine (`engine/setup_detector.py`, `engine/entry.py`)**: Coordinates the "15-Step Continuous Analysis Loop", synthesizing all components to determine signal validity, tier criteria, setup proximity, and ultimate RR-based target calculations. 
- **PerformanceMemory (`engine/performance_memory.py`)**: Retains historical metrics for internal weekly self-audits and behavior adjusting (e.g., stopping a pair if the win-rate plummets).

## Core Mechanisms and Logic Flow

### 1. The Continuous Tick-Processing Loop
The engine establishes a long-lived streaming connection to the broker (OANDA) via `broker/oanda_candles.py`. On each incoming price tick (often multiple times per second):
1. **State Update**: Open positions and pending hit states (TP1, TP2, trailing SL) are updated.
2. **Candle Construction**: If a 1-minute cycle completes, the engine updates its internal structural models.
3. **Multi-Faceted Analysis**: The SetupDetector runs a confluence check across MTF bias, volume confirmation, trend alignment, session availability, and macroeconomic clearance.

### 2. Institutional Validations
- A standard signal requires alignment on five key parameters:
  1. **Multi-Timeframe Validation**: Ensuring trades aren't counter-trend against the 4H/1H structure.
  2. **Zone Mapping**: Proximity to algorithmically mapped Near-Term Supply/Demand or Support/Resistance.
  3. **Liquidity Sweep Focus**: Confirming the algorithm is trading away from a freshly swept liquidity pool (to avoid getting trapped).
  4. **Candle Confirmations**: RSI divergence constraints and robust pattern identifications heavily restrict low-probability setups.
  5. **Macro Window Exclusions**: Blockades immediately surrounding CPI, NFP, rate decisions, etc.

### 3. Reversal Protocol System
The system accommodates extreme market conditions via a defined `"Reversal Catch Protocol"`. If heavily extended (e.g., RSI over/under-extended alongside severe volume divergence inside a major key zone), the Risk Engine dramatically scales down position risk (e.g. up to 0.5%) to attempt a high-RR reversion to the mean.

### 4. Trade Lifecycle and Risk Modeling
Once initiated, a theoretical position is tracked:
- Uses an adaptive Take Profit structure mapping (TP1, TP2, TP3 based on key structural resistance nodes).
- Dynamically activates "Breakeven" arming upon hitting TP1 targets to protect upside.
- PnL (profit and loss) is registered into `output/signals.log` and performance memory is automatically recalibrated.

## The Web Dashboard UI (`dashboard.py`)

A professional, decoupled GUI built with Flask provides a highly transparent overview of the engine's real-time thought process. 
- **Real-Time Streaming**: Implements Server-Sent Events (SSE) to push logs and signals tick-by-tick from the engine to the web interface without requiring page reloads.
- **Live Memory Matrix**: Exposes real-time engine states (e.g., "Approaching Zone", "Waiting Confluence", "Tier A").
- **Economic Visibility**: Pulls upcoming fundamental triggers dynamically.
- **Arty Monitor UI (`templates/arty_monitor.html`)**: Implements a highly robust "glassmorphic" aesthetic monitoring dashboard for deep algorithmic observability.

---

## 🛠️ Infrastructure and Setup Instructions

### Prerequisites
- Python 3.9+
- Activated virtual environment (`.venv`)
- Windows/Linux compatible

### 1. Installation
Clone the repository and install the requirements:
```bash
python -m venv .venv
# Activate: `.venv\Scripts\activate` on Windows, or `source .venv/bin/activate` on MacOS/Linux
pip install -r requirements.txt
```

### 2. Configure Trading Credentials
Create a `.env` file in the project's root directly alongside `main.py`:
```env
OANDA_API_KEY=your_oanda_v20_personal_access_token
OANDA_ACCOUNT_ID=your_oanda_account_identifier
OANDA_ENV=practice  # Use 'live' for real funds, 'practice' for demo accounts
```

### 3. Launching the System
As the engine is heavily asynchronous but decoupled from the dashboard, it requires dual execution for maximum visibility. Open two separate terminals:

**Terminal 1:** Initiate the core decision loops
```bash
python main.py
```
*You will immediately see the engine fetching models, building 4H/1H MTF structures, and binding to the OANDA price streaming API.*

**Terminal 2:** Launch the monitoring dashboard
```bash
python dashboard.py
```

### 4. System Usage
Navigate to `http://localhost:5000` via your preferred web browser. You will see:
- Continuous tick-updates.
- A live ledger of active signals and closed targets.
- Internal ML/Algo state outputs. 

## ⚠️ Disclaimer
**For educational and informational purposes only.** Trading margin-based foreign exchange (FOREX) carries a high level of risk and may not be suitable for all investors. This software provides algorithmic signals based on programmatic rules; the repository owner(s) assumes no liability for loss of real capital. Always test algorithms via practice/paper trading accounts.
