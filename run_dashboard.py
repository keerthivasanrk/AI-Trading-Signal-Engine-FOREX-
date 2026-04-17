"""Streamlit wrapper for recovered forex_trading_system.dashboard_app."""

import os
from dotenv import load_dotenv
load_dotenv(".env", override=True)

# Monkeypatch the compiled settings module to avoid Path(None) errors
import forex_trading_system.config.settings
forex_trading_system.config.settings.Settings.db_path = property(lambda self: 'output/database.sqlite3')
forex_trading_system.config.settings.Settings.data_dir = property(lambda self: 'output')
forex_trading_system.config.settings.Settings.journal_path = property(lambda self: 'output/trade_journal.csv')

import streamlit as st
if "dashboard_state" not in st.session_state:
    st.session_state["dashboard_state"] = {"latest_plans": {}, "latest_data": {}}

from forex_trading_system.dashboard_app import main

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        st.error(f"Render Error: {e}")
        import traceback
        st.code(traceback.format_exc())

