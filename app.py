from datetime import datetime, timezone
import os
import threading
import time
import oandapyV20
from oandapyV20 import API
import oandapyV20.endpoints.instruments as instruments
import pandas as pd
import requests
import streamlit as st

# Page setup
st.set_page_config(
    page_title="Institutional Scanner Terminal", page_icon="⚡", layout="wide"
)

# Credentials
OANDA_ACCESS_TOKEN = (
    "d1c8211fcc0fe62f6c68279e79da11d6-f2d0f1af9b595a5589c6e30559db5712"
)
OANDA_ENV = "practice"
TELEGRAM_BOT_TOKEN = "8701985481:AAFEaUEuEHz0ZpsRNnaXeXUV-sHJzoV8NpE"
TELEGRAM_CHAT_ID = "8891498417"

ASSET_GRID = {
    "Gold (XAU/USD)": "XAU_USD",
    "EUR/USD": "EUR_USD",
    "GBP/USD": "GBP_USD",
    "USD/JPY": "USD_JPY",
    "GBP/JPY": "GBP_JPY",
    "Crude Oil": "WTICO_USD",
}

LOG_FILE = "trade_history.csv"


def log_signal(asset, direction, entry, sl, tp, score, timestamp):
  trade_data = {
      "Timestamp": timestamp,
      "Asset": asset,
      "Signal": direction,
      "Entry": entry,
      "SL": sl,
      "TP": tp,
      "Confluence": f"{score}%",
      "Status": "ACTIVE",
  }
  df = pd.DataFrame([trade_data])
  if not os.path.exists(LOG_FILE):
    df.to_csv(LOG_FILE, index=False)
  else:
    df.to_csv(LOG_FILE, mode="a", header=False, index=False)


def send_telegram_alert(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
  try:
    requests.post(url, json=payload, timeout=5)
  except Exception as e:
    print(f"Telegram error: {e}")


def fetch_oanda_candles(client, instrument, granularity="M1", count=30):
  params = {"count": count, "granularity": granularity, "price": "MBA"}
  req = instruments.InstrumentsCandles(instrument=instrument, params=params)
  try:
    client.request(req)
    candles = req.response.get("candles", [])
    records = []
    for c in candles:
      if c["complete"]:
        records.append({
            "time": c["time"],
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
            "bid_close": float(c["bid"]["c"]),
            "ask_close": float(c["ask"]["c"]),
        })
    return pd.DataFrame(records)
  except Exception as e:
    print(f"OANDA feed error {instrument}: {e}")
    return None


def run_scanner(client):
  timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

  for name, symbol in ASSET_GRID.items():
    df_ltf = fetch_oanda_candles(client, symbol, granularity="M1", count=30)
    df_htf = fetch_oanda_candles(client, symbol, granularity="H1", count=50)

    if df_ltf is None or df_htf is None or df_ltf.empty or df_htf.empty:
      continue

    df_htf["EMA50"] = df_htf["close"].ewm(span=50).mean()
    htf_bullish = df_htf["close"].iloc[-1] > df_htf["EMA50"].iloc[-1]

    recent_high = df_ltf["high"].iloc[-21:-1].max()
    recent_low = df_ltf["low"].iloc[-21:-1].min()
    latest = df_ltf.iloc[-1]

    close_price = latest["close"]
    bull_sweep = (latest["low"] < recent_low) and (close_price > recent_low)
    bear_sweep = (latest["high"] > recent_high) and (close_price < recent_high)

    score = 0
    if htf_bullish:
      score += 35
    if bull_sweep or bear_sweep:
      score += 45
    if 7 <= datetime.now(timezone.utc).hour <= 16:
      score += 20

    if score >= 65 and (bull_sweep or bear_sweep):
      if bull_sweep:
        direction = "🟢 BULLISH SWEEP (BUY)"
        exec_price = latest["ask_close"]
        sl_price = latest["low"] - (exec_price * 0.0002)
        risk = exec_price - sl_price
        tp_price = exec_price + (risk * 2.0)
      else:
        direction = "🔴 BEARISH SWEEP (SELL)"
        exec_price = latest["bid_close"]
        sl_price = latest["high"] + (exec_price * 0.0002)
        risk = sl_price - exec_price
        tp_price = exec_price - (risk * 2.0)

      log_signal(
          name,
          direction,
          exec_price,
          sl_price,
          tp_price,
          score,
          timestamp,
      )

      msg = (
          f"🚨 <b>OANDA INSTITUTIONAL SIGNAL</b> 🚨\n\n"
          f"<b>Asset:</b> {name}\n"
          f"<b>Signal:</b> {direction}\n\n"
          f"<b>Entry (Spread Adjusted):</b> ${exec_price:.4f}\n"
          f"<b>Stop Loss (SL):</b> ${sl_price:.4f}\n"
          f"<b>Take Profit (TP):</b> ${tp_price:.4f} (1:2 RRR)\n\n"
          f"<b>Confluence Score:</b> {score}%\n"
          f"<b>Timestamp:</b> {timestamp}"
      )
      send_telegram_alert(msg)


# Safely start the market scanner background thread ONCE
@st.cache_resource
def start_background_scanner():
  def loop():
    client = API(access_token=OANDA_ACCESS_TOKEN, environment=OANDA_ENV)
    while True:
      try:
        run_scanner(client)
      except Exception as e:
        print(f"Scanner Loop Error: {e}")
      time.sleep(60)

  thread = threading.Thread(target=loop, daemon=True)
  thread.start()
  return thread


# Trigger background engine
start_background_scanner()

# Render Web Dashboard
st.title("⚡ OANDA Institutional Market Terminal")
st.write("Live SMC Liquidity Sweep Engine & Signal Monitor")

# Metrics Cards
col1, col2, col3, col4 = st.columns(4)

if os.path.exists(LOG_FILE):
  df_logs = pd.read_csv(LOG_FILE)
  total_signals = len(df_logs)
else:
  df_logs = pd.DataFrame()
  total_signals = 0

col1.metric("Engine Status", "🟢 ONLINE")
col2.metric("Assets Monitored", len(ASSET_GRID))
col3.metric("Total Signals Fired", total_signals)
col4.metric("Scan Frequency", "60 Seconds")

st.divider()

st.subheader("📋 Trade Signal Performance Log")
if not df_logs.empty:
  st.dataframe(df_logs.iloc[::-1], use_container_width=True)
else:
  st.info(
      "No signals logged yet. The terminal is actively scanning for"
      " high-confluence setups..."
  )
