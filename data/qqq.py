import os

import requests
import pandas as pd

url = "https://data.alpaca.markets/v2/stocks/bars"

api_key = "PKQGZMEOZCJCMUWKH73FYVN4V3"
api_secret = "CQPYmKq49PaNpgLnrN5weWb3pPLMN35SEp7UpzGC43LR"

headers = {
    "APCA-API-KEY-ID": api_key,
    "APCA-API-SECRET-KEY": api_secret,
}

params = {
    "symbols": "UVXY",
    "timeframe": "4Hour",
    "start": "2021-01-01",
    "end": "2026-04-24",
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
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uvxy_4h.csv")
df.to_csv(out_path, index=False)

print(df.head())
print(df.tail())
print("总行数：", len(df))