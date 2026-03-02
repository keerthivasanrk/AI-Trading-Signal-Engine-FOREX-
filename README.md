# AI Trading Signal Engine (FOREX)

A professional real-time FOREX trading signal engine designed for OANDA. It features market structure detection, liquidity sweep analysis, and a visual dashboard for real-time monitoring.

## 🚀 Features

- **Market Structure Engine**: Detects BOS (Break of Structure) and CHoCH (Change of Character).
- **Liquidity Analysis**: Identifies Buy-side and Sell-side liquidity sweeps.
- **5-Gate Entry Filter**:
  1. Multi-Timeframe Bias (H4 & H1 alignment)
  2. Structural Alignment
  3. Liquidity Sweep Confirmation
  4. Trend Filtering (EMA-based)
  5. Relative Volume Confirmation
- **Live Dashboard**:
  - 1-second real-time price updates.
  - Visual delta indicators (price change flashes).
  - Real-time signal table with SSE (Server-Sent Events).
  - Browser & Toast notifications for signals.
- **Risk Management**: Automatic lot size calculation based on account balance and stop-loss distance.
- **News Protection**: Automatically blocks trading during high-impact economic news events (ForexFactory integration).

## 🛠️ Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Credentials**:
   Create a `.env` file in the root directory:
   ```env
   OANDA_API_KEY=your_api_key_here
   OANDA_ACCOUNT_ID=your_account_id_here
   OANDA_ENV=practice
   ```

3. **Run the Engine**:
   Open two terminals:
   ```bash
   # Terminal 1: Core Engine
   python main.py

   # Terminal 2: Web Dashboard
   python dashboard.py
   ```

4. **Access Dashboard**:
   Open `http://localhost:5000` in your browser.

## 📊 Project Structure

- `main.py`: The core processing loop and tick-by-tick logic.
- `dashboard.py`: Flask-based web server for the UI.
- `broker/`: OANDA API handlers and pricing streams.
- `engine/`: Modular components for structure, liquidity, entry, news, and risk.
- `config/`: Global settings and pair configurations.
- `templates/`: Professional glassmorphic dashboard UI.

## ⚠️ Disclaimer

This tool is for educational and informational purposes only. Trading FOREX involves significant risk. Always test in a practice/demo environment before live trading.
