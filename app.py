import os
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
import oandapyV20
from oandapyV20 import API
import oandapyV20.endpoints.instruments as instruments
import pandas as pd
import requests

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


# Simple HTTP Handler to satisfy Render's port binding requirement
class HealthCheckHandler(SimpleHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"OANDA Agent Active")


def run_health_server():
  port = int(os.environ.get("PORT", 10000))
  server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
  server.serve_forever()


def send_telegram_alert(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
  try:
    requests.post(url, json=payload, timeout=5)
  except Exception as e:
    print(f"Telegram error: {e}")


def fetch_oanda_candles(client, instrument, granularity="M1", count=30):
  # Requesting Mid, Bid, and Ask ('MBA') to account for spread
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
  print(f"\n⚡ --- OANDA LIVE SCANNER PASS | {timestamp} ---")

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

    print(
        f"Asset: {name:15s} | Live Close: ${close_price:<9.4f} | Confluence:"
        f" {score}%"
    )

    if score >= 65 and (bull_sweep or bear_sweep):
      if bull_sweep:
        direction = "🟢 BULLISH SWEEP (BUY)"
        exec_price = latest["ask_close"]  # Actual Buy entry (Ask)
        sl_price = latest["low"] - (exec_price * 0.0002)  # Below sweep low
        risk = exec_price - sl_price
        tp_price = exec_price + (risk * 2.0)  # 1:2 RRR
      else:
        direction = "🔴 BEARISH SWEEP (SELL)"
        exec_price = latest["bid_close"]  # Actual Sell entry (Bid)
        sl_price = latest["high"] + (exec_price * 0.0002)  # Above sweep high
        risk = sl_price - exec_price
        tp_price = exec_price - (risk * 2.0)  # 1:2 RRR

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


def start_loop():
  client = API(access_token=OANDA_ACCESS_TOKEN, environment=OANDA_ENV)
  print("Connected to OANDA API. Agent loop starting...")
  while True:
    try:
      run_scanner(client)
    except Exception as e:
      print(f"Execution Error: {e}")
    time.sleep(60)


if __name__ == "__main__":
  # Start Web Server in background for Render health check
  threading.Thread(target=run_health_server, daemon=True).start()
  # Run continuous trading loop
  start_loop()
