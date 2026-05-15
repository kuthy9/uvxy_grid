"""data/vxx.py — 拉 VXX 4h 前 5 年 Alpaca 数据.

复用 data/qqq.py 的 pattern. VXX (iPath Series B S&P 500 VIX Short-Term Futures ETN)
是战术化"理论最优"对照标的 (1× VIX 期货, 高波动 + 强 mean-revert).
输出列与 data/uvxy_4h.csv 对齐: c,h,l,n,o,t,v,vw
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import requests

api_key = "PKQGZMEOZCJCMUWKH73FYVN4V3"
api_secret = "CQPYmKq49PaNpgLnrN5weWb3pPLMN35SEp7UpzGC43LR"

url = "https://data.alpaca.markets/v2/stocks/bars"

headers = {
    "APCA-API-KEY-ID": api_key,
    "APCA-API-SECRET-KEY": api_secret,
}

end_dt = datetime(2026, 5, 15)
start_dt = end_dt - timedelta(days=365 * 5)

params = {
    "symbols": "VXX",
    "timeframe": "4Hour",
    "start": start_dt.strftime("%Y-%m-%d"),
    "end": end_dt.strftime("%Y-%m-%d"),
    "feed": "iex",
    "limit": 10000,
}

all_bars = []
page_token = None

while True:
    if page_token:
        params["page_token"] = page_token
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    data = r.json()
    bars = data.get("bars", {}).get("VXX", [])
    all_bars.extend(bars)
    page_token = data.get("next_page_token")
    if not page_token:
        break

df = pd.DataFrame(all_bars)
# 显式投影列顺序对齐 uvxy_4h.csv (防 Alpaca 偶尔加新列)
df = df[["c", "h", "l", "n", "o", "t", "v", "vw"]]
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vxx_4h.csv")
df.to_csv(out_path, index=False)

print(df.head())
print(df.tail())
print(f"总行数: {len(df)}")
print(f"窗口: {df['t'].min()} → {df['t'].max()}")
