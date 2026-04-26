import os
import sys

import requests
import pandas as pd

url = "https://data.alpaca.markets/v2/stocks/bars"

api_key = os.getenv("APCA_API_KEY_ID")
api_secret = os.getenv("APCA_API_SECRET_KEY")
if not api_key or not api_secret:
    sys.exit(
        "缺少 Alpaca 凭据: 请设置环境变量 APCA_API_KEY_ID / APCA_API_SECRET_KEY 后重试. "
        "可在 https://alpaca.markets 控制台获取."
    )

headers = {
    "APCA-API-KEY-ID": api_key,
    "APCA-API-SECRET-KEY": api_secret,
}

params = {
    "symbols": "UVXY",
    "timeframe": "15Min",
    "start": "2021-01-01",
    "end": "2026-01-01",
    "feed": "iex",
    "limit": 10000
}

all_bars = []
page_token = None

while True:
    if page_token:
        params["page_token"] = page_token

    r = requests.get(url, headers=headers, params=params)
    data = r.json()

    bars = data["bars"]["UVXY"]
    all_bars.extend(bars)

    page_token = data.get("next_page_token")
    if not page_token:
        break

df = pd.DataFrame(all_bars)
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uvxy_15min.csv")
df.to_csv(out_path, index=False)

print(df.head())
print(df.tail())
print("总行数：", len(df))