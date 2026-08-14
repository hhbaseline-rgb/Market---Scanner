from datetime import datetime, timedelta, timezone
import os
import threading
import time
import oandapyV20
from oandapyV20 import API
import oandapyV20.endpoints.instruments as instruments
import pandas as pd
import requests
import streamlit as st

# Credentials & Config
OANDA_ACCESS_TOKEN = (
    "d1c8211fcc0fe62f6c68279e79da11d6-f2d0f1af9b595a5589c6e30559db5712"
)
OANDA_ENV = "practice"
TELEGRAM_BOT_TOKEN = "8701985481:AAFEaUEuEHz0ZpsRNnaXeXUV-sHJzoV8NpE"
TELEGRAM_CHAT_ID = "8891498417"

# FOREX MAJORS & CROSSES ONLY
ASSET_GRID = {
    "EUR/USD": "EUR_USD",
    "GBP/USD": "GBP_USD",
    "USD/JPY": "USD_JPY",
    "GBP/JPY": "GBP_JPY",
    "AUD/USD": "AUD_USD",
    "USD/CAD": "USD_CAD",
}

# Persistent storage compatibility (for Render disk or local)
LOG_FILE = (
    "/var/data/trade_history.csv"
    if os.path.exists("/var/data")
    else "trade_history.csv"
)
COOLDOWN_TRACKER = {}


def log_signal(asset, direction, entry, sl, tp, score, timestamp, notes=""):
  trade_data = {
      "Timestamp": timestamp,
      "Asset": asset,
      "Signal": direction,
      "Entry": round(entry, 5),
      "SL": round(sl, 5),
      "TP": round(tp, 5),
      "Confluence": f"{score}%",
      "Status": "ACTIVE",
      "Setup": notes,
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


def fetch_oanda_candles(client, instrument, granularity="H1", count=50):
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


def calculate_atr(df, period=14):
  high_low = df["high"] - df["low"]
  high_cp = (df["high"] - df["close"].shift(1)).abs()
  low_cp = (df["low"] - df["close"].shift(1)).abs()
  tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
  return tr.rolling(period).mean().iloc[-1]


def evaluate_open_trades(client):
  if not os.path.exists(LOG_FILE):
    return

  df = pd.read_csv(LOG_FILE)
  if df.empty or "Status" not in df.columns:
    return

  active_trades = df[df["Status"] == "ACTIVE"]
  if active_trades.empty:
    return

  updated = False
  for idx, row in active_trades.iterrows():
    asset_name = row["Asset"]
    symbol = ASSET_GRID.get(asset_name)
    if not symbol:
      continue

    candles = fetch_oanda_candles(client, symbol, granularity="H1", count=2)
    if candles is None or candles.empty:
      continue

    latest = candles.iloc[-1]
    curr_high = latest["high"]
    curr_low = latest["low"]

    entry = float(row["Entry"])
    sl = float(row["SL"])
    tp = float(row["TP"])
    is_buy = "BULLISH" in str(row["Signal"])

    if is_buy:
      if curr_high >= tp:
        df.at[idx, "Status"] = "WIN 🟢"
        updated = True
      elif curr_low <= sl:
        df.at[idx, "Status"] = "LOSS 🔴"
        updated = True
    else:
      if curr_low <= tp:
        df.at[idx, "Status"] = "WIN 🟢"
        updated = True
      elif curr_high >= sl:
        df.at[idx, "Status"] = "LOSS 🔴"
        updated = True

  if updated:
    df.to_csv(LOG_FILE, index=False)


def run_scanner_cycle(client):
  now_utc = datetime.now(timezone.utc)
  timestamp_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

  evaluate_open_trades(client)

  for name, symbol in ASSET_GRID.items():
    # 8-Hour Cooldown per asset to enforce strict patience
    if symbol in COOLDOWN_TRACKER:
      if now_utc - COOLDOWN_TRACKER[symbol] < timedelta(hours=8):
        continue

    # Fetch Daily (D) for Macro Liquidity Pools and H1 for Structure
    df_daily = fetch_oanda_candles(client, symbol, granularity="D", count=10)
    df_h1 = fetch_oanda_candles(client, symbol, granularity="H1", count=50)

    if (
        df_daily is None
        or df_h1 is None
        or len(df_daily) < 2
        or len(df_h1) < 20
    ):
      continue

    # 1. Macro Liquidity Levels (Previous Day High & Low)
    pdh = df_daily["high"].iloc[-2]
    pdl = df_daily["low"].iloc[-2]

    # 2. H1 Market Structure Confirmation
    latest_h1 = df_h1.iloc[-1]
    h1_atr = calculate_atr(df_h1)

    # Bullish: Price raided Previous Day Low, but closed back above
    pd_bull_raid = (latest_h1["low"] < pdl) and (latest_h1["close"] > pdl)

    # Bearish: Price raided Previous Day High, but closed back below
    pd_bear_raid = (latest_h1["high"] > pdh) and (latest_h1["close"] < pdh)

    if pd_bull_raid or pd_bear_raid:
      score = 90  # High macro confluence

      if pd_bull_raid:
        direction = "🟢 BULLISH MACRO REVERSAL (BUY)"
        exec_price = latest_h1["ask_close"]
        sl_price = min(latest_h1["low"], pdl) - (h1_atr * 0.75)
        risk = exec_price - sl_price
        tp_price = exec_price + (risk * 2.0)
        setup_note = "Daily Low Raid + H1 Reversion"

      else:
        direction = "🔴 BEARISH MACRO REVERSAL (SELL)"
        exec_price = latest_h1["bid_close"]
        sl_price = max(latest_h1["high"], pdh) + (h1_atr * 0.75)
        risk = sl_price - exec_price
        tp_price = exec_price - (risk * 2.0)
        setup_note = "Daily High Raid + H1 Reversion"

      log_signal(
          name,
          direction,
          exec_price,
          sl_price,
          tp_price,
          score,
          timestamp_str,
          setup_note,
      )
      COOLDOWN_TRACKER[symbol] = now_utc

      msg = (
          f"🏆 <b>INSTITUTIONAL MACRO SIGNAL</b> 🏆\n\n"
          f"<b>Asset:</b> {name}\n"
          f"<b>Setup:</b> {setup_note}\n"
          f"<b>Signal:</b> {direction}\n\n"
          f"<b>Entry Price:</b> ${exec_price:.5f}\n"
          f"<b>Macro Stop Loss:</b> ${sl_price:.5f}\n"
          f"<b>Take Profit Target:</b> ${tp_price:.5f} (1:2 RRR)\n\n"
          f"<b>Confluence Score:</b> {score}%\n"
          f"<b>Timestamp:</b> {timestamp_str}"
      )
      send_telegram_alert(msg)


def background_worker():
  client = API(access_token=OANDA_ACCESS_TOKEN, environment=OANDA_ENV)
  print("Macro Liquidity Structural Engine Started.")
  while True:
    try:
      run_scanner_cycle(client)
    except Exception as e:
      print(f"Scanner Loop Error: {e}")
    time.sleep(300)  # Runs cycle every 5 minutes


if not any(
    thread.name == "OandaScannerThread" for thread in threading.enumerate()
):
  scanner_thread = threading.Thread(
      target=background_worker, name="OandaScannerThread", daemon=True
  )
  scanner_thread.start()

# --- STREAMLIT DASHBOARD UI ---
st.set_page_config(
    page_title="Macro Structural Terminal", page_icon="🏛️", layout="wide"
)

st.title("🏛️ Institutional Macro Terminal (Daily / H1)")
st.write(
    "Selling the Shovels: Tracking Daily Liquidity Raids & Macro Trend"
    " Expansions"
)

if os.path.exists(LOG_FILE):
  df_logs = pd.read_csv(LOG_FILE)
  total_signals = len(df_logs)
  wins = len(df_logs[df_logs["Status"] == "WIN 🟢"])
  losses = len(df_logs[df_logs["Status"] == "LOSS 🔴"])
  resolved = wins + losses
  win_rate = f"{(wins / resolved * 100):.1f}%" if resolved > 0 else "N/A"
else:
  df_logs = pd.DataFrame()
  total_signals = 0
  wins = 0
  losses = 0
  win_rate = "N/A"

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Engine Status", "🟢 ONLINE")
col2.metric("Assets Monitored", len(ASSET_GRID))
col3.metric("Total Macro Signals", total_signals)
col4.metric("Win Rate", win_rate)
col5.metric("Wins / Losses", f"{wins}W - {losses}L")

st.divider()

st.subheader("📋 Macro Signal Performance Journal")
if not df_logs.empty:
  st.dataframe(df_logs.iloc[::-1], use_container_width=True)
else:
  st.info(
      "No signals logged yet. Patiently monitoring Previous Day High/Low raids"
      " on H1 timeframe..."
  )
