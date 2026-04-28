#!/usr/bin/env python3
"""Fetch daily OHLCV via yfinance, calculate RSI signals, update docs/data.json"""

import json
import datetime
import os
import yfinance as yf
import numpy as np

STOCKS = {
    "3608": {
        "name": "ＴＳＩホールディングス",
        "sector": "Textiles",
        "net_debt": "-45B",
        "op_margin": "1.2%",
        "cf_growth": "N/A"
    },
    "8841": {
        "name": "テーオーシー",
        "sector": "Real Estate",
        "net_debt": "-28B",
        "op_margin": "19.8%",
        "cf_growth": "N/A"
    },
    "6419": {
        "name": "マースグループホールディングス",
        "sector": "Machinery",
        "net_debt": "-36B",
        "op_margin": "29.2%",
        "cf_growth": "N/A"
    },
    "6118": {
        "name": "アイダエンジニアリング",
        "sector": "Machinery",
        "net_debt": "-33B",
        "op_margin": "5.9%",
        "cf_growth": "N/A"
    },
    "7292": {
        "name": "村上開明堂",
        "sector": "Auto/Transport",
        "net_debt": "-47B",
        "op_margin": "7.5%",
        "cf_growth": "N/A"
    },
    "7860": {
        "name": "エイベックス",
        "sector": "IT/Telecom",
        "net_debt": "-36B",
        "op_margin": "4.9%",
        "cf_growth": "N/A"
    },
    "7211": {
        "name": "三菱自動車工業",
        "sector": "Auto/Transport",
        "net_debt": "-138B",
        "op_margin": "2.0%",
        "cf_growth": "N/A"
    },
    "9726": {
        "name": "ＫＮＴ－ＣＴホールディングス",
        "sector": "Services",
        "net_debt": "-12B",
        "op_margin": "3.9%",
        "cf_growth": "N/A"
    }
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
        pbr = info.get("priceToBook")
        pbr = str(round(pbr, 2)) if pbr else "—"
        industry = info.get("industry", "")
        mc = info.get("marketCap", 0)
        mkt_cap_raw = mc
        if mc >= 1e12:
            mkt_cap = f"¥{mc/1e12:.1f}T"
        elif mc >= 1e9:
            mkt_cap = f"¥{mc/1e9:.0f}B"
        else:
            mkt_cap = f"¥{mc/1e6:.0f}M"
    except Exception:
        beta = "—"
        pbr = "—"
        mkt_cap = "—"
        industry = ""
        mkt_cap_raw = 0
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

    # Dividend data (past 1 year from history)
    import pandas as pd
    div_yield = None
    div_per_share = None
    div_records = []
    try:
        divs = t.dividends
        if not divs.empty:
            cutoff = pd.Timestamp.now(tz="Asia/Tokyo") - pd.Timedelta(days=365)
            recent = divs[divs.index >= cutoff]
            if not recent.empty:
                annual = float(recent.sum())
                div_per_share = round(annual, 1)
                div_yield = round(annual / last_close * 100, 2) if last_close > 0 else None
                for d, v in recent.items():
                    div_records.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "amount": round(float(v), 1),
                    })
    except Exception:
        pass

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
        "pbr": pbr,
        "mkt_cap": mkt_cap,
        "price": round(last_close, 1),
        "unit_cost": unit_cost,
        "rsi": round(current_rsi, 1),
        "signal": signal,
        "sma20": round(sma20, 1),
        "div_yield": div_yield,
        "div_per_share": div_per_share,
        "div_records": div_records,
        "chart": chart_data,
        "_industry": industry,
        "_net_cash_val": net_cash_val if net_cash_positive else 0,
        "_mkt_cap_raw": mkt_cap_raw,
    }


def generate_quantitative_tags(stock_data):
    """Generate tags from numeric data."""
    tags = []
    # Net Cash
    if stock_data.get("net_cash_positive"):
        nc = stock_data.get("_net_cash_val", 0)
        mc = stock_data.get("_mkt_cap_raw", 1)
        if mc > 0 and nc / mc > 0.3:
            tags.append("Cash Rich")
        elif mc > 0 and nc / mc > 0.1:
            tags.append("Net Cash")
    # PBR
    try:
        pbr = float(stock_data.get("pbr", "0"))
        if pbr > 0 and pbr < 1.0:
            tags.append("Deep Value")
        elif pbr > 0 and pbr < 1.5:
            tags.append("Value")
    except (ValueError, TypeError):
        pass
    # Beta
    try:
        beta = float(stock_data.get("beta", "0"))
        if beta > 0 and beta < 0.5:
            tags.append("Low Vol")
        elif beta > 1.2:
            tags.append("High Vol")
    except (ValueError, TypeError):
        pass
    # Op Margin
    try:
        margin = float(stock_data.get("op_margin", "0").replace("%", ""))
        if margin >= 20:
            tags.append("High Margin")
        elif margin >= 15:
            tags.append("Good Margin")
    except (ValueError, TypeError):
        pass
    return tags


def generate_qualitative_tags(stock_data, quantitative_tags):
    """Generate qualitative tags via Claude API. Falls back to sector only."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Fallback: use yfinance industry
        industry = stock_data.get("_industry", "")
        return [industry] if industry else []

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Given this Japanese stock, generate 2-3 short qualitative tags (max 2 words each) for an investment dashboard.
Tags should describe growth drivers, business model strengths, or thematic exposure.
Do NOT repeat these existing tags: {quantitative_tags}

Stock: {stock_data['code']} {stock_data['name']}
Sector: {stock_data['sector']}
Industry: {stock_data.get('_industry', 'N/A')}
Market Cap: {stock_data.get('mkt_cap', 'N/A')}
Op Margin: {stock_data.get('op_margin', 'N/A')}

Return ONLY a JSON array of strings, e.g. ["Global Growth", "Automation"]. No explanation."""

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        result = message.content[0].text.strip()
        # Strip markdown code fences if present
        if result.startswith("```"):
            result = result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(result)
    except Exception as e:
        print(f"  Claude API fallback for {stock_data['code']}: {e}")
        industry = stock_data.get("_industry", "")
        return [industry] if industry else []


def generate_tags(stock_data):
    """Combine sector, quantitative, and qualitative tags (in that order)."""
    sector = [stock_data.get("sector", "")]
    sector = [s for s in sector if s]
    quant = generate_quantitative_tags(stock_data)
    qual = generate_qualitative_tags(stock_data, sector + quant)
    return sector + quant + qual


def main():
    today = datetime.date.today().isoformat()
    ni225 = fetch_ni225()
    dyn_stop = STOP_BASE if ni225["uptrend"] else STOP_TIGHT

    stocks = []
    for code in STOCKS:
        print(f"Fetching {code}...")
        data = fetch_stock(code)
        if data:
            print(f"  Generating tags...")
            data["tags"] = generate_tags(data)
            print(f"  Tags: {data['tags']}")
            # Remove internal fields
            for k in list(data.keys()):
                if k.startswith("_"):
                    del data[k]
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
