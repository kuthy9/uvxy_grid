"""data/multi_pull.py — 批量 Alpaca 4h × 5y 拉数.

Usage:
    python data/multi_pull.py SYM1 SYM2 ... SYMN

输出 data/{sym_lower}_4h.csv, 列 = c,h,l,n,o,t,v,vw (与 uvxy_4h.csv 对齐).
失败的 symbol 跳过, 不中断. 凭证按 CLAUDE.md §10 与 qqq.py / vxx.py 同源.
"""
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

api_key = "PKQGZMEOZCJCMUWKH73FYVN4V3"
api_secret = "CQPYmKq49PaNpgLnrN5weWb3pPLMN35SEp7UpzGC43LR"

SYMBOLS = [s.upper() for s in sys.argv[1:]]
if not SYMBOLS:
    print("usage: python data/multi_pull.py SYM1 SYM2 ...")
    sys.exit(1)

end_dt = datetime(2026, 5, 15)
start_dt = end_dt - timedelta(days=365 * 5)

url = "https://data.alpaca.markets/v2/stocks/bars"
headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
EXPECTED_COLS = ["c", "h", "l", "n", "o", "t", "v", "vw"]

success, failed = [], []
for sym in SYMBOLS:
    params = {
        "symbols": sym,
        "timeframe": "4Hour",
        "start": start_dt.strftime("%Y-%m-%d"),
        "end": end_dt.strftime("%Y-%m-%d"),
        "feed": "iex",
        "limit": 10000,
    }
    all_bars, page_token = [], None
    try:
        while True:
            if page_token:
                params["page_token"] = page_token
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code != 200:
                print(f"  {sym}: HTTP {r.status_code}, skip")
                break
            data = r.json()
            bars = data.get("bars", {}).get(sym, [])
            all_bars.extend(bars)
            page_token = data.get("next_page_token")
            if not page_token:
                break
    except requests.RequestException as e:
        print(f"  {sym}: request error {e}, skip")
        failed.append(sym)
        continue

    if not all_bars:
        print(f"  {sym}: 0 bars, skip")
        failed.append(sym)
        continue

    df = pd.DataFrame(all_bars)
    df = df[[c for c in EXPECTED_COLS if c in df.columns]]
    out_path = os.path.join(DATA_DIR, f"{sym.lower()}_4h.csv")
    df.to_csv(out_path, index=False)
    win_start = df["t"].min() if len(df) else "?"
    win_end = df["t"].max() if len(df) else "?"
    print(f"  {sym}: {len(df)} bars ({win_start} → {win_end}) → {out_path}")
    success.append(sym)
    time.sleep(0.3)

print(f"\n汇总: 成功 {len(success)} / 失败 {len(failed)}")
if failed:
    print(f"  失败: {failed}")
