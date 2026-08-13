import os
from datetime import datetime, timezone
import time
import oandapyV20
from oandapyV20 import API
import oandapyV20.endpoints.instruments as instruments
import pandas as pd
import requests

# Direct OANDA Credentials
OANDA_ACCESS_TOKEN = "d1c8211fcc0fe62f6c68279e79da11d6-f2d0f1af9b595a5589c6e30559db5712"
OANDA_ENV = "practice"  # 'practice' for demo feeds, 'live' for real funds

# Telegram Alert Credentials
TELEGRAM_BOT_TOKEN = "8701985481:AAFEaUEuEHz0ZpsRNnaXeXUV-sHJzoV8NpE"
TELEGRAM_CHAT_ID = "8891498417"

# Real-Time Asset Tracking Grid
ASSET_GRID = {
    "Gold (XAU/USD)": "XAU_USD",
    "EUR/USD": "EUR_USD",
    "GBP/USD": "GBP_USD",
    "USD/JPY": "USD_JPY",
    "GBP/JPY": "GBP_JPY",
    "Crude Oil": "WTICO_USD",
}


def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram alert error: {e}")


def fetch_oanda_candles(client, instrument, granularity="M1", count=30):
    params = {"count": count, "granularity": granularity}
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
                })
        return pd.DataFrame(records)
    except Exception as e:
        print(f"Failed to fetch OANDA feed for {instrument}: {e}")
        return None


def run_scanner(client):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n⚡ --- OANDA LIVE SCANNER PASS | {timestamp} ---")

    for name, symbol in ASSET_GRID.items():
        # Fetch 1-minute LTF and 1-hour HTF data
        df_ltf = fetch_oanda_candles(client, symbol, granularity="M1", count=30)
        df_htf = fetch_oanda_candles(client, symbol, granularity="H1", count=50)

        if df_ltf is None or df_htf is None or df_ltf.empty or df_htf.empty:
            continue

        # 1-Hour HTF Trend Alignment (50 EMA)
        df_htf["EMA50"] = df_htf["close"].ewm(span=50).mean()
        htf_bullish = df_htf["close"].iloc[-1] > df_htf["EMA50"].iloc[-1]

        # Liquidity Sweeps on 1-Min Bar
        recent_high = df_ltf["high"].iloc[-21:-1].max()
        recent_low = df_ltf["low"].iloc[-21:-1].min()
        latest = df_ltf.iloc[-1]
        close_price = latest["close"]

        bull_sweep = (latest["low"] < recent_low) and (close_price > recent_low)
        bear_sweep = (latest["high"] > recent_high) and (close_price < recent_high)

        # Confluence Score Calculation
        score = 0
        if htf_bullish:
            score += 35
        if bull_sweep or bear_sweep:
            score += 45
        if 7 <= datetime.now(timezone.utc).hour <= 16:
            score += 20

        print(f"Asset: {name:15s} | Live Close: ${close_price:<9.4f} | Confluence: {score}%")

        # High-Conviction Dispatch Trigger
        if score >= 65 and (bull_sweep or bear_sweep):
            direction = "🟢 BULLISH SWEEP" if bull_sweep else "🔴 BEARISH SWEEP"
            msg = (
                f"🚨 <b>OANDA INSTITUTIONAL SIGNAL</b> 🚨\n\n"
                f"<b>Asset:</b> {name}\n"
                f"<b>Signal:</b> {direction}\n"
                f"<b>Execution Price:</b> ${close_price:.4f}\n"
                f"<b>Confluence Score:</b> {score}%\n"
                f"<b>Timestamp:</b> {timestamp}"
            )
            send_telegram_alert(msg)


if __name__ == "__main__":
    print("Initiating OANDA Live Connection...")
    client = API(access_token=OANDA_ACCESS_TOKEN, environment=OANDA_ENV)
    print("Connected successfully. Market Agent fully active.")

    while True:
        try:
            run_scanner(client)
        except Exception as e:
            print(f"Execution Error: {e}")
        time.sleep(60)  # Runs every 60 seconds on the dot
