from datetime import datetime, time as dtime, timezone
import time
import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# Page Configuration
st.set_page_config(
    page_title="Relentless Market Grid", page_icon="⚡", layout="wide"
)

# Telegram Settings
TELEGRAM_BOT_TOKEN = "8701985481:AAFEaUEuEHz0ZpsRNnaXeXUV-sHJzoV8NpE"
TELEGRAM_CHAT_ID = "8891498417"

MARKET_GRID = {
    "Gold (XAU/USD)": "GC=F",
    "Silver (XAG/USD)": "SI=F",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "GBP/JPY": "GBPJPY=X",
    "AUD/USD": "AUDUSD=X",
    "Crude Oil": "CL=F",
    "S&P 500": "^GSPC",
    "Nasdaq 100": "^IXIC",
}


def send_telegram_alert(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
  try:
    requests.post(url, json=payload, timeout=5)
  except Exception as e:
    st.error(f"Telegram Alert Error: {e}")


def analyze_asset(name, ticker):
  try:
    df_ltf = yf.download(
        tickers=ticker,
        period="5d",
        interval="15m",
        auto_adjust=True,
        progress=False,
    )
    df_htf = yf.download(
        tickers=ticker,
        period="1mo",
        interval="1h",
        auto_adjust=True,
        progress=False,
    )

    if df_ltf.empty or df_htf.empty:
      return None

    if isinstance(df_ltf.columns, pd.MultiIndex):
      df_ltf.columns = df_ltf.columns.get_level_values(0)
      df_htf.columns = df_htf.columns.get_level_values(0)

    # ATR Calculation
    hl = df_ltf["High"] - df_ltf["Low"]
    hc = np.abs(df_ltf["High"] - df_ltf["Close"].shift())
    lc = np.abs(df_ltf["Low"] - df_ltf["Close"].shift())
    df_ltf["ATR"] = (
        pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()
    )

    # HTF Trend
    df_htf["EMA50"] = df_htf["Close"].ewm(span=50).mean()
    htf_bullish = df_htf["Close"].iloc[-1] > df_htf["EMA50"].iloc[-1]

    # Liquidity Sweep
    recent_high = df_ltf["High"].shift(1).rolling(20).max().iloc[-1]
    recent_low = df_ltf["Low"].shift(1).rolling(20).min().iloc[-1]
    latest = df_ltf.iloc[-1]

    bull_sweep = (latest["Low"] < recent_low) and (latest["Close"] > recent_low)
    bear_sweep = (latest["High"] > recent_high) and (
        latest["Close"] < recent_high
    )

    # Confluence Scoring
    score = 0
    if htf_bullish:
      score += 30
    if bull_sweep or bear_sweep:
      score += 45

    now_utc = datetime.now(timezone.utc).time()
    if dtime(7, 0) <= now_utc <= dtime(16, 0):
      score += 25  # Prime session bonus

    latest_close = float(latest["Close"])
    latest_atr = float(latest["ATR"])

    if score >= 60:
      direction = "BULLISH 🟢" if (htf_bullish or bull_sweep) else "BEARISH 🔴"
      msg = (
          f"⚡ <b>DASHBOARD ALERT TRIGGERED</b> ⚡\n\n"
          f"<b>Asset:</b> {name}\n"
          f"<b>Bias:</b> {direction}\n"
          f"<b>Current Price:</b> ${latest_close:.2f}\n"
          f"<b>Confluence Score:</b> {score}%\n"
          f"<b>ATR Volatility:</b> ${latest_atr:.4f}"
      )
      send_telegram_alert(msg)

    return {
        "Asset": name,
        "Price": f"${latest_close:.2f}",
        "Score": f"{score}%",
        "HTF Trend": "🟢 Bullish" if htf_bullish else "🔴 Bearish",
        "Liquidity Sweep": "✅ Yes" if (bull_sweep or bear_sweep) else "❌ No",
        "Raw Score": score,
    }
  except Exception:
    return None


# --- STREAMLIT DASHBOARD UI ---
st.title("⚡ Relentless Market Intelligence Grid")
st.write(
    f"**Live Engine Status:** Active | **UTC Time:**"
    f" {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
)

# Control Sidebar
auto_refresh = st.sidebar.checkbox("Enable Auto-Scan Loop (60s)", value=True)
run_now = st.sidebar.button("Run Instant Manual Scan")

# Run Grid Scan
results = []
progress_bar = st.progress(0)
status_text = st.empty()

assets_list = list(MARKET_GRID.items())
for i, (name, ticker) in enumerate(assets_list):
  status_text.text(f"Scanning {name}...")
  res = analyze_asset(name, ticker)
  if res:
    results.append(res)
  progress_bar.progress((i + 1) / len(assets_list))

status_text.text("Scan complete!")

if results:
  df_results = pd.DataFrame(results)

  # Highlights Metric Cards
  top_signal = df_results.sort_values(by="Raw Score", ascending=False).iloc[0]

  col1, col2, col3 = st.columns(3)
  col1.metric("Highest Confluence Asset", top_signal["Asset"])
  col2.metric("Top Setup Score", top_signal["Score"])
  col3.metric("Current Market Price", top_signal["Price"])

  st.subheader("Global Market Liquidity Grid")

  # Color highlight high confluence setups
  def highlight_high_score(val):
    raw = int(val.replace("%", ""))
    if raw >= 60:
      return "background-color: #1b4332; color: #74c69d; font-weight: bold"
    elif raw >= 30:
      return "background-color: #2b2d42; color: #edf2f4"
    return "color: #8d99ae"

  styled_df = df_results[
      ["Asset", "Price", "Score", "HTF Trend", "Liquidity Sweep"]
  ].style.applymap(highlight_high_score, subset=["Score"])

  st.dataframe(styled_df, use_container_width=True)

if auto_refresh:
  time.sleep(60)
  st.rerun()
