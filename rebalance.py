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
RATE_LIMIT_DELAY = 0.5       # Seconds between yfinance calls (individual)
BATCH_DELAY = 0.5            # Seconds between batch downloads
BATCH_SIZE = 50              # yfinance batch download size (price fetch)
MAX_RETRIES = 3              # Retry count for failed fetches
RETRY_DELAY = 5              # Seconds between retries
CACHE_FILE = "docs/screening.json"

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
        for attempt in range(MAX_RETRIES):
            try:
                data = yf.download(tickers_str, period="1d", progress=False, threads=True)
                if data.empty:
                    break
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
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    print(f"  Batch {i} retry {attempt+1}/{MAX_RETRIES}: {e}")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"  Batch {i} failed after {MAX_RETRIES} retries: {e}")
        if i % 100 == 0 and i > 0:
            print(f"  ... {i}/{len(codes)} done ({len(results)} prices)")
        time.sleep(BATCH_DELAY)

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


BS_REFRESH_DAYS = 90  # Force balance sheet refresh every 90 days (quarterly)


def load_cache():
    """Load previous screening results for differential scanning."""
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        cached = {s["code"]: s for s in data.get("all_passed", [])}
        cache_date = data.get("date", "2000-01-01")
        days_old = (datetime.date.today() - datetime.date.fromisoformat(cache_date)).days
        force_bs = days_old >= BS_REFRESH_DAYS
        print(f"  Cache loaded: {len(cached)} stocks from {cache_date} ({days_old}d ago)")
        if force_bs:
            print(f"  Cache > {BS_REFRESH_DAYS} days old → forcing full BS refresh (quarterly)")
        return cached, force_bs
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        print("  No cache found, full scan required")
        return {}, True


def fetch_fundamentals(candidates):
    """Step 5: Fetch balance sheet and info. Uses cache for known net-cash stocks.
    Forces full BS refresh every 90 days (quarterly earnings cycle)."""
    cache, force_bs = load_cache()
    cached_codes = set(cache.keys())

    if force_bs:
        # Quarterly: treat all as new (full BS scan)
        cached_candidates = []
        new_candidates = candidates
    else:
        cached_candidates = [c for c in candidates if c["code"] in cached_codes]
        new_candidates = [c for c in candidates if c["code"] not in cached_codes]

    mode = "QUARTERLY FULL SCAN" if force_bs else f"{len(cached_candidates)} cached + {len(new_candidates)} new"
    print(f"[5/8] Fetching fundamentals: {mode}")
    results = []

    def fetch_one(code, c, need_bs=True):
        """Fetch single stock data. need_bs=False skips balance sheet (cached)."""
        for attempt in range(MAX_RETRIES):
            try:
                t = yf.Ticker(f"{code}.T")
                info = t.info

                mkt_cap = info.get("marketCap", 0)
                if mkt_cap < MIN_MARKET_CAP:
                    return None

                pbr = info.get("priceToBook", 99) or 99
                op_margin = info.get("operatingMargins", 0) or 0
                beta = info.get("beta", 1) or 1
                industry = info.get("industry", "")

                if need_bs:
                    bs = t.balance_sheet
                    if bs.empty:
                        return None
                    col = bs.columns[0]
                    debt = float(bs.loc["Total Debt", col]) if "Total Debt" in bs.index else 0
                    cash_val = float(bs.loc["Cash And Cash Equivalents", col]) if "Cash And Cash Equivalents" in bs.index else 0
                    net_cash = cash_val - debt
                    if net_cash <= 0:
                        return None
                else:
                    # Use cached score, just verify mkt_cap still OK
                    prev = cache.get(code, {})
                    net_cash = prev.get("net_cash", 0)
                    if net_cash is None or net_cash <= 0:
                        # Cache invalid, do full fetch
                        return fetch_one(code, c, need_bs=True)

                sector = SECTOR_MAP.get(c["sector_jp"], c["sector_jp"])
                net_cash_ratio = net_cash / mkt_cap if mkt_cap > 0 else 0
                score = (
                    net_cash_ratio * 40 +
                    max(0, (2 - pbr)) * 20 +
                    op_margin * 20 +
                    max(0, (1.5 - beta)) * 20
                )

                # 1-month price data
                hist = t.history(period="1mo")
                if not hist.empty:
                    month_high = float(hist["High"].max())
                    month_low = float(hist["Low"].min())
                    month_open = float(hist["Close"].iloc[0])
                    month_change = (c["price"] / month_open - 1) * 100 if month_open > 0 else 0
                else:
                    month_high = c["price"]
                    month_low = c["price"]
                    month_change = 0

                # RSI(14) from 3-month history
                hist_rsi = t.history(period="3mo")
                rsi_val = None
                if len(hist_rsi) > 14:
                    closes = hist_rsi["Close"].values
                    deltas = np.diff(closes)
                    gains = np.where(deltas > 0, deltas, 0.0)
                    losses = np.where(deltas < 0, -deltas, 0.0)
                    avg_g = np.mean(gains[:14])
                    avg_l = np.mean(losses[:14])
                    for j in range(14, len(deltas)):
                        avg_g = (avg_g * 13 + gains[j]) / 14
                        avg_l = (avg_l * 13 + losses[j]) / 14
                    rs_val = avg_g / avg_l if avg_l != 0 else 100
                    rsi_val = round(100 - 100 / (1 + rs_val), 1)

                return {
                    "code": code, "name": c["name"], "sector": sector,
                    "sector_jp": c["sector_jp"], "market": c["market"],
                    "industry": industry, "price": c["price"],
                    "unit_cost": int(c["price"] * 100), "mkt_cap": mkt_cap,
                    "net_cash": net_cash, "net_cash_ratio": net_cash_ratio,
                    "pbr": pbr, "op_margin": op_margin, "beta": beta,
                    "month_high": month_high, "month_low": month_low,
                    "month_change": round(month_change, 2), "rsi": rsi_val,
                    "score": score,
                }
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
        return None

    # Phase A: Quick update for cached stocks (skip balance sheet)
    phase_a = []
    print(f"  Phase A: {len(cached_candidates)} cached stocks (skip BS)...")
    for i, c in enumerate(cached_candidates):
        r = fetch_one(c["code"], c, need_bs=False)
        if r:
            results.append(r)
            phase_a.append(r)
        if (i + 1) % 20 == 0:
            print(f"    ... {i+1}/{len(cached_candidates)} done, {len(results)} passed")
        time.sleep(RATE_LIMIT_DELAY)
    if phase_a:
        supabase_upsert_batch(phase_a, "Phase A cached")

    # Phase B: Full scan for new stocks (upsert every 20 stocks)
    phase_b_batch = []
    print(f"  Phase B: {len(new_candidates)} new stocks (full scan)...")
    for i, c in enumerate(new_candidates):
        r = fetch_one(c["code"], c, need_bs=True)
        if r:
            results.append(r)
            phase_b_batch.append(r)
        # Upsert every 20 passed stocks
        if len(phase_b_batch) >= 20:
            supabase_upsert_batch(phase_b_batch, f"Phase B batch@{i+1}")
            phase_b_batch = []
        if (i + 1) % 20 == 0:
            print(f"    ... {i+1}/{len(new_candidates)} done, {len(results)} passed")
        time.sleep(RATE_LIMIT_DELAY)
    if phase_b_batch:
        supabase_upsert_batch(phase_b_batch, "Phase B final")

    print(f"  Total: {len(results)} net-cash positive stocks")
    return results, force_bs


def calc_rs(results):
    """Calculate Relative Strength (1-99) based on 1-month change percentile."""
    print("[6/8] Calculating RS...")
    changes = [r["month_change"] for r in results]
    if not changes:
        return
    sorted_changes = sorted(changes)
    n = len(sorted_changes)
    for r in results:
        rank = sorted_changes.index(r["month_change"])
        r["rs"] = max(1, min(99, int(rank / n * 98) + 1))
    print(f"  RS range: {min(changes):.1f}% (RS 1) to {max(changes):.1f}% (RS 99)")


def select_universe(results, force_bs=False):
    """Step 7: Score, rank, select top N, mark strategy."""
    print(f"[7/8] Selecting top {MAX_STOCKS} for net_cash_select...")
    results.sort(key=lambda x: x["score"], reverse=True)

    for r in results:
        r["strategy"] = None
    for r in results[:MAX_STOCKS]:
        r["strategy"] = "net_cash_select"

    selected = results[:MAX_STOCKS]
    print(f"\n{'='*60}")
    print(f"  SELECTED UNIVERSE ({len(selected)} stocks)")
    print(f"{'='*60}")
    for i, s in enumerate(selected, 1):
        print(f"  {i}. {s['code']} {s['name']} RS={s.get('rs','-')}")
        print(f"     Score={s['score']:.2f} NC/MC={s['net_cash_ratio']:.1%} PBR={s['pbr']:.1f} Margin={s['op_margin']:.1%}")

    if len(results) > MAX_STOCKS:
        print(f"\n  Runners-up:")
        for s in results[MAX_STOCKS:MAX_STOCKS + 5]:
            print(f"    {s['code']} {s['name']}: Score={s['score']:.2f} RS={s.get('rs','-')}")

    # Patch update_data.py
    stocks_dict = {}
    for s in selected:
        nc = s.get("net_cash", 0)
        if nc is None or (isinstance(nc, float) and np.isnan(nc)):
            nc = 0
        nc_str = f"-{nc/1e12:.1f}T" if nc >= 1e12 else f"-{nc/1e9:.0f}B" if nc >= 1e9 else f"-{nc/1e6:.0f}M"
        om_val = s.get("op_margin", 0) or 0
        om = f"{om_val*100:.1f}%" if om_val and not (isinstance(om_val, float) and np.isnan(om_val)) else "N/A"
        stocks_dict[s["code"]] = {
            "name": s["name"],
            "sector": s["sector"],
            "net_debt": nc_str,
            "op_margin": om,
            "cf_growth": "N/A",
        }

    import re
    stocks_str = "STOCKS = " + json.dumps(stocks_dict, indent=4, ensure_ascii=False)
    with open("update_data.py", "r") as f:
        content = f.read()
    content = re.sub(r'STOCKS = \{.*?\n\}', stocks_str, content, count=1, flags=re.DOTALL)
    with open("update_data.py", "w") as f:
        f.write(content)
    print(f"\n  Patched update_data.py with {len(selected)} stocks.")

    # Save screening results
    with open("docs/screening.json", "w") as f:
        json.dump({
            "date": datetime.date.today().isoformat(),
            "bs_refreshed": force_bs,
            "net_cash_positive": len(results),
            "selected": len(selected),
            "universe": [
                {k: (_safe_round(v, 2) if isinstance(v, float) else v)
                 for k, v in s.items() if k not in ("industry",)}
                for s in selected
            ],
            "all_passed": [
                {"code": s["code"], "name": s["name"], "score": round(s["score"], 2),
                 "rs": s.get("rs"), "net_cash": _safe_int(s.get("net_cash"))}
                for s in results
            ],
        }, f, indent=2, ensure_ascii=False)
    print("  Saved docs/screening.json")

    return results


def _supabase_headers():
    """Get Supabase connection info. Returns (endpoint, headers) or (None, None)."""
    base_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not base_url or not service_key:
        return None, None
    endpoint = f"{base_url}/rest/v1/jpx_stocks"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Content-Profile": "public",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    return endpoint, headers


def _safe_int(v):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return int(v)
    except (ValueError, TypeError):
        return None

def _safe_round(v, n=2):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return round(float(v), n)
    except (ValueError, TypeError):
        return None

def _to_row(s):
    """Convert stock dict to Supabase row."""
    return {
        "code": s["code"],
        "name": s["name"],
        "market": s.get("market", ""),
        "sector_jp": s.get("sector_jp", ""),
        "sector": s.get("sector", ""),
        "price": _safe_round(s.get("price"), 1),
        "unit_cost": _safe_int(s.get("unit_cost")),
        "market_cap": _safe_int(s.get("mkt_cap")),
        "net_cash": _safe_int(s.get("net_cash")),
        "net_cash_ratio": _safe_round(s.get("net_cash_ratio"), 4),
        "pbr": _safe_round(s.get("pbr"), 2) if s.get("pbr") != 99 else None,
        "op_margin": _safe_round(s.get("op_margin"), 4),
        "beta": _safe_round(s.get("beta"), 2) if s.get("beta") != 1 else None,
        "month_high": _safe_round(s.get("month_high"), 1),
        "month_low": _safe_round(s.get("month_low"), 1),
        "month_change": _safe_round(s.get("month_change"), 2),
        "rs": _safe_int(s.get("rs")),
        "rsi": _safe_round(s.get("rsi"), 1),
        "score": _safe_round(s.get("score"), 2),
        "strategy": s.get("strategy"),
        "signal": s.get("signal", "WAIT"),
        "tags": s.get("tags"),
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def supabase_upsert_batch(stocks, label=""):
    """Upsert a batch of stocks to Supabase. Can be called at each phase."""
    endpoint, headers = _supabase_headers()
    if not endpoint:
        return

    rows = [_to_row(s) for s in stocks]
    batch_size = 50
    ok = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        body = json.dumps(batch, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            urllib.request.urlopen(req)
            ok += len(batch)
        except Exception as e:
            print(f"  [Supabase] upsert batch {i} failed: {e}")

    print(f"  [Supabase] {label}: upserted {ok}/{len(rows)} rows")


def supabase_cleanup(valid_codes):
    """Remove stocks no longer in the net-cash universe."""
    endpoint, headers = _supabase_headers()
    if not endpoint:
        return

    # Fetch all codes currently in DB
    fetch_headers = {**headers, "Accept": "application/json"}
    fetch_headers.pop("Prefer", None)
    req = urllib.request.Request(f"{endpoint}?select=code", headers=fetch_headers)
    try:
        resp = urllib.request.urlopen(req).read()
        db_codes = {r["code"] for r in json.loads(resp)}
    except Exception:
        return

    stale = db_codes - set(valid_codes)
    if not stale:
        print("  [Supabase] cleanup: no stale rows")
        return

    # Delete stale in batches
    del_headers = {k: v for k, v in headers.items() if k != "Prefer"}
    del_headers["Prefer"] = "return=minimal"
    for code in stale:
        req = urllib.request.Request(f"{endpoint}?code=eq.{code}", method="DELETE", headers=del_headers)
        try:
            urllib.request.urlopen(req)
        except Exception:
            pass
    print(f"  [Supabase] cleanup: removed {len(stale)} stale rows")


def main():
    print("=" * 60)
    print("  NET CASH SELECT — MONTHLY REBALANCE")
    print(f"  {datetime.date.today().isoformat()}")
    print("=" * 60)

    # Step 1-2: Download and filter JPX list
    df = download_jpx_list()
    df = filter_basic(df)
    codes = [str(c) for c in df["コード"].tolist()]

    # Step 3-4: Fetch prices, filter by unit price
    prices = fetch_prices_batch(codes)
    candidates = filter_by_price(df, prices)

    # Step 5: Fetch fundamentals
    results, force_bs = fetch_fundamentals(candidates)

    # Step 6: Calculate RS
    calc_rs(results)

    # Step 7: Select universe, patch update_data.py
    results = select_universe(results, force_bs=force_bs)

    # Step 8: Final upsert (strategy/RS/score) + cleanup stale rows
    supabase_upsert_batch(results, "Final (strategy+RS)")
    supabase_cleanup([r["code"] for r in results])

    print("\nDone.")


if __name__ == "__main__":
    main()
