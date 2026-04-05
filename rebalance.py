#!/usr/bin/env python3
"""Monthly rebalance: screen TSE stocks for net-cash-rich deep value candidates.

Pipeline:
  1. Download JPX listed stocks (xls)
  2. Filter: Prime/Standard, non-financial, domestic
  3. Fetch market cap via yfinance (batch, with rate limiting)
  4. Filter: 100 shares < 1M yen, market cap > 50B
  5. Fetch balance sheet for survivors → net cash filter
  6. Score and rank → select top N
  7. Patch update_data.py STOCKS dict
"""

import json
import os
import time
import datetime
import urllib.request
import io
import yfinance as yf
import numpy as np
import pandas as pd


# === Config ===
MAX_UNIT_PRICE = 1_000_000   # 100 shares < 1M yen
MIN_MARKET_CAP = 50e9        # > 50B yen
MAX_STOCKS = 8               # Final portfolio size
RATE_LIMIT_DELAY = 0.3       # Seconds between yfinance calls
BATCH_SIZE = 20              # yfinance batch download size

# Financial sectors to exclude
EXCLUDE_SECTORS = [
    "銀行業", "証券、商品先物取引業", "保険業", "その他金融業",
]

# Sector name mapping (Japanese industry → short English tag)
SECTOR_MAP = {
    "電気機器": "Electronics",
    "機械": "Machinery",
    "化学": "Chemicals",
    "情報・通信業": "IT/Telecom",
    "サービス業": "Services",
    "小売業": "Retail",
    "卸売業": "Wholesale",
    "食料品": "Food",
    "輸送用機器": "Auto/Transport",
    "精密機器": "Precision",
    "医薬品": "Pharma",
    "金属製品": "Metals",
    "建設業": "Construction",
    "不動産業": "Real Estate",
    "繊維製品": "Textiles",
    "ガラス・土石製品": "Glass/Ceramics",
    "ゴム製品": "Rubber",
    "非鉄金属": "Non-Ferrous",
    "鉄鋼": "Steel",
    "石油・石炭製品": "Oil/Coal",
    "パルプ・紙": "Paper",
    "水産・農林業": "Agriculture",
    "鉱業": "Mining",
    "倉庫・運輸関連業": "Logistics",
    "陸運業": "Land Transport",
    "海運業": "Shipping",
    "空運業": "Airlines",
    "電気・ガス業": "Utilities",
}


def download_jpx_list():
    """Step 1: Download JPX listed stocks."""
    print("[1/6] Downloading JPX stock list...")
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req).read()
    df = pd.read_excel(io.BytesIO(data))
    print(f"  Downloaded {len(df)} entries")
    return df


def filter_basic(df):
    """Step 2: Filter to Prime/Standard domestic non-financial stocks."""
    print("[2/6] Basic filtering...")
    # Domestic Prime + Standard only
    mask = df["市場・商品区分"].isin(["プライム（内国株式）", "スタンダード（内国株式）"])
    df = df[mask]
    # Exclude financial sectors
    df = df[~df["33業種区分"].isin(EXCLUDE_SECTORS)]
    print(f"  {len(df)} stocks (Prime+Standard, non-financial)")
    return df


def fetch_prices_batch(codes):
    """Step 3: Fetch current prices and market caps in batches."""
    print(f"[3/6] Fetching prices for {len(codes)} stocks in batches of {BATCH_SIZE}...")
    results = {}
    for i in range(0, len(codes), BATCH_SIZE):
        batch = codes[i:i + BATCH_SIZE]
        tickers_str = " ".join(f"{c}.T" for c in batch)
        try:
            data = yf.download(tickers_str, period="1d", progress=False, threads=True)
            if data.empty:
                continue
            # Handle single vs multi ticker response
            if len(batch) == 1:
                close = data["Close"].iloc[-1] if not data["Close"].empty else 0
                results[batch[0]] = {"price": float(close)}
            else:
                for code in batch:
                    ticker = f"{code}.T"
                    try:
                        close = float(data["Close"][ticker].iloc[-1])
                        if not np.isnan(close):
                            results[code] = {"price": close}
                    except (KeyError, IndexError):
                        pass
        except Exception as e:
            print(f"  Batch error at {i}: {e}")
        if i % 100 == 0 and i > 0:
            print(f"  ... {i}/{len(codes)} done")
        time.sleep(RATE_LIMIT_DELAY)

    print(f"  Got prices for {len(results)} stocks")
    return results


def filter_by_price(df, prices):
    """Step 4: Filter by unit price and basic market cap."""
    print("[4/6] Filtering by unit price...")
    passed = []
    for _, row in df.iterrows():
        code = str(row["コード"])
        if code not in prices:
            continue
        price = prices[code]["price"]
        if price * 100 > MAX_UNIT_PRICE:
            continue
        if price <= 0:
            continue
        passed.append({
            "code": code,
            "name": str(row["銘柄名"]),
            "sector_jp": str(row["33業種区分"]),
            "market": str(row["市場・商品区分"]),
            "price": price,
        })
    print(f"  {len(passed)} stocks with 100 shares < ¥{MAX_UNIT_PRICE:,}")
    return passed


def fetch_fundamentals(candidates):
    """Step 5: Fetch balance sheet and info for candidates. Rate-limited."""
    print(f"[5/6] Fetching fundamentals for {len(candidates)} candidates...")
    results = []
    for i, c in enumerate(candidates):
        code = c["code"]
        try:
            t = yf.Ticker(f"{code}.T")
            info = t.info
            bs = t.balance_sheet

            # Market cap filter
            mkt_cap = info.get("marketCap", 0)
            if mkt_cap < MIN_MARKET_CAP:
                continue

            if bs.empty:
                continue

            col = bs.columns[0]
            debt = float(bs.loc["Total Debt", col]) if "Total Debt" in bs.index else 0
            cash = float(bs.loc["Cash And Cash Equivalents", col]) if "Cash And Cash Equivalents" in bs.index else 0
            net_cash = cash - debt

            if net_cash <= 0:
                continue

            pbr = info.get("priceToBook", 99) or 99
            op_margin = info.get("operatingMargins", 0) or 0
            beta = info.get("beta", 1) or 1
            industry = info.get("industry", "")

            # Sector mapping
            sector = SECTOR_MAP.get(c["sector_jp"], c["sector_jp"])

            # Composite score
            net_cash_ratio = net_cash / mkt_cap if mkt_cap > 0 else 0
            score = (
                net_cash_ratio * 40 +
                max(0, (2 - pbr)) * 20 +
                op_margin * 20 +
                max(0, (1.5 - beta)) * 20
            )

            results.append({
                "code": code,
                "name": c["name"],
                "sector": sector,
                "industry": industry,
                "price": c["price"],
                "mkt_cap": mkt_cap,
                "net_cash": net_cash,
                "net_cash_ratio": net_cash_ratio,
                "pbr": pbr,
                "op_margin": op_margin,
                "beta": beta,
                "score": score,
            })

            if len(results) % 10 == 0:
                print(f"  ... {i+1}/{len(candidates)} scanned, {len(results)} passed")

        except Exception as e:
            pass  # Silently skip problematic stocks

        time.sleep(RATE_LIMIT_DELAY)

    print(f"  {len(results)} stocks are net-cash positive with mkt cap > ¥{MIN_MARKET_CAP/1e9:.0f}B")
    return results


def select_and_update(results):
    """Step 6: Score, rank, select top N, update update_data.py."""
    print(f"[6/6] Selecting top {MAX_STOCKS}...")
    results.sort(key=lambda x: x["score"], reverse=True)
    selected = results[:MAX_STOCKS]

    print(f"\n{'='*60}")
    print(f"  SELECTED UNIVERSE ({len(selected)} stocks)")
    print(f"{'='*60}")
    for i, s in enumerate(selected, 1):
        print(f"  {i}. {s['code']} {s['name']}")
        print(f"     Score={s['score']:.2f} NC/MC={s['net_cash_ratio']:.1%} PBR={s['pbr']:.1f} Margin={s['op_margin']:.1%} Beta={s['beta']:.2f}")

    # Also show runners-up
    if len(results) > MAX_STOCKS:
        print(f"\n  Runners-up:")
        for s in results[MAX_STOCKS:MAX_STOCKS+5]:
            print(f"    {s['code']} {s['name']}: Score={s['score']:.2f}")

    # Build STOCKS dict for update_data.py
    stocks_dict = {}
    for s in selected:
        nc = s["net_cash"]
        if nc >= 1e12:
            nc_str = f"-{nc/1e12:.1f}T"
        elif nc >= 1e9:
            nc_str = f"-{nc/1e9:.0f}B"
        else:
            nc_str = f"-{nc/1e6:.0f}M"

        om = f"{s['op_margin']*100:.1f}%" if s["op_margin"] else "N/A"

        stocks_dict[s["code"]] = {
            "name": s["name"],
            "sector": s["sector"],
            "net_debt": nc_str,
            "op_margin": om,
            "cf_growth": "N/A",
        }

    # Patch update_data.py
    import re
    stocks_str = "STOCKS = " + json.dumps(stocks_dict, indent=4, ensure_ascii=False)

    with open("update_data.py", "r") as f:
        content = f.read()

    content = re.sub(
        r'STOCKS = \{.*?\n\}',
        stocks_str,
        content,
        count=1,
        flags=re.DOTALL,
    )

    with open("update_data.py", "w") as f:
        f.write(content)

    print(f"\n  Patched update_data.py with {len(selected)} stocks.")

    # Save screening results
    with open("docs/screening.json", "w") as f:
        json.dump({
            "date": datetime.date.today().isoformat(),
            "scanned_total": "JPX Prime+Standard",
            "net_cash_positive": len(results),
            "selected": len(selected),
            "universe": [
                {
                    "code": s["code"],
                    "name": s["name"],
                    "sector": s["sector"],
                    "score": round(s["score"], 2),
                    "net_cash_ratio": round(s["net_cash_ratio"], 4),
                    "pbr": round(s["pbr"], 2),
                    "op_margin": round(s["op_margin"] * 100, 1),
                    "beta": round(s["beta"], 2),
                }
                for s in selected
            ],
            "all_passed": [
                {"code": s["code"], "name": s["name"], "score": round(s["score"], 2)}
                for s in results
            ],
        }, f, indent=2, ensure_ascii=False)
    print("  Saved docs/screening.json")


def main():
    print("=" * 60)
    print("  NET CASH SELECT — MONTHLY REBALANCE")
    print(f"  {datetime.date.today().isoformat()}")
    print("=" * 60)

    # Step 1: Download JPX list
    df = download_jpx_list()

    # Step 2: Basic filter
    df = filter_basic(df)
    codes = [str(c) for c in df["コード"].tolist()]

    # Step 3: Fetch prices (batch, fast)
    prices = fetch_prices_batch(codes)

    # Step 4: Filter by unit price
    candidates = filter_by_price(df, prices)

    # Step 5: Fetch fundamentals (slower, rate-limited)
    results = fetch_fundamentals(candidates)

    # Step 6: Select and update
    select_and_update(results)

    print("\nDone.")


if __name__ == "__main__":
    main()
