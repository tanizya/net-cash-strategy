#!/usr/bin/env python3
"""Monthly rebalance: screen TSE stocks for net-cash-rich deep value candidates.
Runs at month-end to update the stock universe in update_data.py."""

import json
import os
import yfinance as yf
import numpy as np


# Screening criteria
MIN_NET_CASH = 0            # Net cash positive (cash > debt)
MAX_UNIT_PRICE = 1_000_000  # 100 shares < 1M yen
MIN_MARKET_CAP = 50e9       # > 50B yen (liquidity)
MAX_STOCKS = 8              # Portfolio size cap

# Current universe (baseline)
CANDIDATES = [
    "6383", "2127", "3087", "6592", "7564",
    # Additional scan pool
    "5444", "6857", "6834", "4091", "3397", "9697",
    "7867", "2782", "4980", "9434", "6055", "6101",
    "3040", "4186", "8697", "6758", "7741", "6273",
    "7752", "4684", "6645", "7731", "6503", "7272",
]


def score_stock(code):
    """Score a stock for inclusion in the universe. Higher = better."""
    ticker = f"{code}.T"
    try:
        t = yf.Ticker(ticker)
        info = t.info
        bs = t.balance_sheet

        if bs.empty:
            return None

        # Price filter
        price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
        if price * 100 > MAX_UNIT_PRICE:
            return None

        # Market cap filter
        mkt_cap = info.get("marketCap", 0)
        if mkt_cap < MIN_MARKET_CAP:
            return None

        # Net cash calculation
        col = bs.columns[0]
        debt = float(bs.loc["Total Debt", col]) if "Total Debt" in bs.index else 0
        cash = float(bs.loc["Cash And Cash Equivalents", col]) if "Cash And Cash Equivalents" in bs.index else 0
        net_cash = cash - debt

        if net_cash < MIN_NET_CASH:
            return None

        # Scoring components
        net_cash_ratio = net_cash / mkt_cap if mkt_cap > 0 else 0
        pbr = info.get("priceToBook", 99)
        op_margin = info.get("operatingMargins", 0) or 0
        beta = info.get("beta", 1) or 1

        # Composite score: favor high cash ratio, low PBR, high margin, low beta
        score = (
            net_cash_ratio * 40 +           # Cash richness (40% weight)
            max(0, (2 - pbr)) * 20 +        # Value (20% weight, PBR < 2 gets points)
            op_margin * 20 +                 # Profitability (20% weight)
            max(0, (1.5 - beta)) * 20        # Stability (20% weight)
        )

        name = info.get("shortName", code)
        industry = info.get("industry", "")
        sector_map = {
            "Specialty Industrial Machinery": "Automation",
            "Capital Markets": "Advisory",
            "Restaurants": "F&B",
            "Auto Parts": "Motors",
            "Apparel Retail": "Retail",
        }
        sector = sector_map.get(industry, industry.split()[-1] if industry else "Other")

        return {
            "code": code,
            "name": name,
            "sector": sector,
            "price": price,
            "mkt_cap": mkt_cap,
            "net_cash": net_cash,
            "net_cash_ratio": net_cash_ratio,
            "pbr": pbr,
            "op_margin": op_margin,
            "beta": beta,
            "score": score,
        }
    except Exception as e:
        print(f"  {code}: Error - {e}")
        return None


def generate_stocks_dict(selected):
    """Generate the STOCKS dict for update_data.py."""
    stocks = {}
    for s in selected:
        nc = s["net_cash"]
        if nc >= 1e12:
            nc_str = f"-{nc/1e12:.1f}T"
        elif nc >= 1e9:
            nc_str = f"-{nc/1e9:.0f}B"
        else:
            nc_str = f"-{nc/1e6:.0f}M"

        om = f"{s['op_margin']*100:.1f}%" if s["op_margin"] else "N/A"
        stocks[s["code"]] = {
            "name": s["name"],
            "sector": s["sector"],
            "net_debt": nc_str,
            "op_margin": om,
            "cf_growth": "N/A",
        }
    return stocks


def update_stocks_in_script(selected):
    """Patch STOCKS dict in update_data.py."""
    stocks_dict = generate_stocks_dict(selected)
    stocks_str = "STOCKS = " + json.dumps(stocks_dict, indent=4, ensure_ascii=False)

    with open("update_data.py", "r") as f:
        content = f.read()

    import re
    content = re.sub(
        r'STOCKS = \{.*?\n\}',
        stocks_str,
        content,
        count=1,
        flags=re.DOTALL,
    )

    with open("update_data.py", "w") as f:
        f.write(content)


def main():
    print("=== Monthly Rebalance Screening ===")
    print(f"Scanning {len(CANDIDATES)} candidates...\n")

    results = []
    for code in CANDIDATES:
        print(f"  Screening {code}...", end="")
        result = score_stock(code)
        if result:
            print(f" Score={result['score']:.2f} NC={result['net_cash']/1e9:.0f}B PBR={result['pbr']:.1f}")
            results.append(result)
        else:
            print(" EXCLUDED")

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    # Select top N
    selected = results[:MAX_STOCKS]

    print(f"\n=== Selected Universe ({len(selected)} stocks) ===")
    for i, s in enumerate(selected, 1):
        print(f"  {i}. {s['code']} {s['name']}: Score={s['score']:.2f} NC/MC={s['net_cash_ratio']:.1%} PBR={s['pbr']:.1f} Margin={s['op_margin']:.1%}")

    # Update update_data.py
    update_stocks_in_script(selected)
    print(f"\nUpdated STOCKS in update_data.py with {len(selected)} stocks.")

    # Save screening results
    with open("docs/screening.json", "w") as f:
        json.dump({
            "date": __import__("datetime").date.today().isoformat(),
            "scanned": len(CANDIDATES),
            "passed": len(results),
            "selected": len(selected),
            "universe": [{"code": s["code"], "name": s["name"], "score": round(s["score"], 2),
                          "net_cash_ratio": round(s["net_cash_ratio"], 4), "pbr": round(s["pbr"], 2)}
                         for s in selected],
            "all_passed": [{"code": s["code"], "name": s["name"], "score": round(s["score"], 2)}
                           for s in results],
        }, f, indent=2, ensure_ascii=False)
    print("Saved docs/screening.json")


if __name__ == "__main__":
    main()
