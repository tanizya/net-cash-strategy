#!/usr/bin/env python3
"""Fetch daily OHLCV via yfinance, calculate RSI signals, update docs/data.json"""

import json
import datetime
import yfinance as yf
import numpy as np

STOCKS = {
    "6383": {"name": "Daifuku", "sector": "Automation", "net_debt": "-193B", "op_margin": "10.2%", "cf_growth": "+18%"},
    "2127": {"name": "M&A Center", "sector": "Advisory", "net_debt": "-8B", "op_margin": "32.5%", "cf_growth": "+25%"},
    "3087": {"name": "Doutor Nichires", "sector": "F&B", "net_debt": "-12B", "op_margin": "7.8%", "cf_growth": "+12%"},
    "6592": {"name": "Mabuchi Motor", "sector": "Motors", "net_debt": "-142B", "op_margin": "14.6%", "cf_growth": "-3%"},
    "7564": {"name": "Workman", "sector": "Retail", "net_debt": "-93B", "op_margin": "16.1%", "cf_growth": "+8%"},
}

RSI_LEN = 14
RSI_OVERSOLD = 35
RSI_TAKEPROFIT = 65
SMA_EXIT_LEN = 20
NI225_SMA_LEN = 50
STOP_BASE = 10.0
STOP_TIGHT = 7.0


def calc_rsi(closes, length=14):
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:length])
    avg_loss = np.mean(losses[:length])
    rsi_values = []
    for i in range(length, len(deltas)):
        avg_gain = (avg_gain * (length - 1) + gains[i]) / length
        avg_loss = (avg_loss * (length - 1) + losses[i]) / length
        rs = avg_gain / avg_loss if avg_loss != 0 else 100
        rsi_values.append(100 - 100 / (1 + rs))
    return rsi_values


def fetch_ni225():
    t = yf.Ticker("^N225")
    h = t.history(period="1y")
    closes = h["Close"].values
    sma50 = np.mean(closes[-NI225_SMA_LEN:]) if len(closes) >= NI225_SMA_LEN else np.mean(closes)
    return {
        "close": float(closes[-1]),
        "sma50": float(sma50),
        "uptrend": bool(closes[-1] > sma50),
    }


def fetch_stock(code):
    ticker = f"{code}.T"
    t = yf.Ticker(ticker)
    h = t.history(period="1y")

    if h.empty:
        return None

    closes = h["Close"].values
    highs = h["High"].values
    lows = h["Low"].values
    opens = h["Open"].values
    volumes = h["Volume"].values
    dates = h.index

    # RSI
    rsi_vals = calc_rsi(closes, RSI_LEN)
    current_rsi = rsi_vals[-1] if rsi_vals else 50
    prev_rsi = rsi_vals[-2] if len(rsi_vals) > 1 else 50

    # RSI crossover check
    rsi_cross_up = prev_rsi < RSI_OVERSOLD and current_rsi >= RSI_OVERSOLD

    # SMA for exit
    sma20 = float(np.mean(closes[-SMA_EXIT_LEN:])) if len(closes) >= SMA_EXIT_LEN else float(np.mean(closes))
    sma_breakdown = closes[-1] < sma20 and closes[-2] >= sma20 if len(closes) >= 2 else False

    # Signal status
    signal = "BUY" if rsi_cross_up else "WAIT"

    # Price & Beta & Net Cash
    last_close = float(closes[-1])
    last_open = float(opens[-1])
    unit_cost = int(last_close * 100)
    try:
        info = t.info
        beta = info.get("beta")
        beta = str(round(beta, 2)) if beta else "—"
        mc = info.get("marketCap", 0)
        if mc >= 1e12:
            mkt_cap = f"¥{mc/1e12:.1f}T"
        elif mc >= 1e9:
            mkt_cap = f"¥{mc/1e9:.0f}B"
        else:
            mkt_cap = f"¥{mc/1e6:.0f}M"
    except Exception:
        beta = "—"
        mkt_cap = "—"
    try:
        bs = t.balance_sheet
        col = bs.columns[0]
        debt = float(bs.loc["Total Debt", col]) if "Total Debt" in bs.index else 0
        cash = float(bs.loc["Cash And Cash Equivalents", col]) if "Cash And Cash Equivalents" in bs.index else 0
        net_cash_val = cash - debt
        sign = "+" if net_cash_val >= 0 else "-"
        abs_val = abs(net_cash_val)
        if abs_val >= 1e12:
            net_cash_str = f"{sign}¥{abs_val/1e12:.1f}T"
        elif abs_val >= 1e9:
            net_cash_str = f"{sign}¥{abs_val/1e9:.0f}B"
        else:
            net_cash_str = f"{sign}¥{abs_val/1e6:.0f}M"
        net_cash_positive = net_cash_val >= 0
    except Exception:
        net_cash_str = "—"
        net_cash_positive = False

    # Chart data (sample every 5 bars for mini chart)
    chart_data = []
    for i in range(0, len(closes), 5):
        d = dates[i]
        chart_data.append({
            "time": d.strftime("%Y-%m-%d"),
            "value": round(float(closes[i]), 1),
        })
    # Always include last bar
    if dates[-1].strftime("%Y-%m-%d") != chart_data[-1]["time"]:
        chart_data.append({
            "time": dates[-1].strftime("%Y-%m-%d"),
            "value": round(float(closes[-1]), 1),
        })

    return {
        "code": code,
        "name": STOCKS[code]["name"],
        "sector": STOCKS[code]["sector"],
        "net_debt": net_cash_str,
        "net_cash_positive": net_cash_positive,
        "op_margin": STOCKS[code]["op_margin"],
        "cf_growth": STOCKS[code]["cf_growth"],
        "beta": beta,
        "mkt_cap": mkt_cap,
        "price": round(last_close, 1),
        "unit_cost": unit_cost,
        "rsi": round(current_rsi, 1),
        "signal": signal,
        "sma20": round(sma20, 1),
        "chart": chart_data,
    }


def main():
    today = datetime.date.today().isoformat()
    ni225 = fetch_ni225()
    dyn_stop = STOP_BASE if ni225["uptrend"] else STOP_TIGHT

    stocks = []
    for code in STOCKS:
        print(f"Fetching {code}...")
        data = fetch_stock(code)
        if data:
            stocks.append(data)

    output = {
        "updated": today,
        "ni225": ni225,
        "stop_pct": dyn_stop,
        "stocks": stocks,
    }

    with open("docs/data.json", "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Also update inline LIVE_DATA in index.html
    # Sample chart data to every 10th point for compact inline
    compact = {
        "updated": output["updated"],
        "ni225": {"close": round(ni225["close"], 1), "sma50": round(ni225["sma50"], 1), "uptrend": ni225["uptrend"]},
        "stop_pct": output["stop_pct"],
        "stocks": [],
    }
    for s in stocks:
        cs = {k: s[k] for k in s if k != "chart"}
        cs["chart"] = s["chart"][::2]  # every other point
        if s["chart"][-1] not in cs["chart"]:
            cs["chart"].append(s["chart"][-1])
        compact["stocks"].append(cs)

    inline_json = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

    index_path = "docs/index.html"
    with open(index_path, "r") as f:
        html = f.read()

    import re
    html = re.sub(
        r'const LIVE_DATA = \{.*?\};',
        f'const LIVE_DATA = {inline_json};',
        html,
        count=1,
        flags=re.DOTALL,
    )

    with open(index_path, "w") as f:
        f.write(html)

    print(f"Updated docs/data.json and docs/index.html ({today})")
    print(f"NI225: {ni225['close']:.0f} (SMA50: {ni225['sma50']:.0f}, {'UP' if ni225['uptrend'] else 'DOWN'})")
    for s in stocks:
        print(f"  {s['code']} {s['name']}: RSI={s['rsi']:.1f} Signal={s['signal']} Price={s['price']}")


if __name__ == "__main__":
    main()
