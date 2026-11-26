"""Download Binance spot klines: monthly zips from data.binance.vision + REST for the current month."""
import io, zipfile, time, datetime as dt
from pathlib import Path
import pandas as pd, requests

COLS = ["open_time","open","high","low","close","volume","close_time","quote_vol","trades","tb_base","tb_quote","ignore"]
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def _to_ms(s):
    s = pd.to_numeric(s)
    return s.where(s < 1e14, s // 1000)  # 2025+ files use microseconds

def _monthly(symbol, interval, year, month):
    url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip"
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(df.iloc[0, 0]).isdigit():  # header row
        df = df.iloc[1:]
    df.columns = COLS
    return df

def _rest(symbol, interval, start_ms):
    out = []
    while True:
        r = requests.get("https://api.binance.com/api/v3/klines",
                         params=dict(symbol=symbol, interval=interval, startTime=start_ms, limit=1000), timeout=30)
        r.raise_for_status(); rows = r.json()
        if not rows: break
        out += rows; start_ms = rows[-1][0] + 1
        if len(rows) < 1000: break
        time.sleep(0.2)
    return pd.DataFrame(out, columns=COLS)

def download(symbol, interval, start="2020-01-01"):
    start = pd.Timestamp(start); today = pd.Timestamp.now("UTC").tz_localize(None)
    frames, months = [], pd.period_range(start, today, freq="M")
    last_ok = None
    for p in months:
        df = _monthly(symbol, interval, p.year, p.month)
        if df is None: break
        frames.append(df); last_ok = p
    nxt = (last_ok + 1).to_timestamp() if last_ok is not None else start
    frames.append(_rest(symbol, interval, int(nxt.timestamp() * 1000)))
    df = pd.concat(frames, ignore_index=True)
    df["open_time"] = _to_ms(df["open_time"])
    df = df[["open_time","open","high","low","close","volume"]].astype(float)
    df["time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms")
    df = df.drop_duplicates("time").set_index("time").sort_index().drop(columns="open_time")
    # drop the still-forming last candle
    step = pd.Timedelta(interval)
    df = df[df.index + step <= today]
    return df

def load(symbol, interval):
    return pd.read_parquet(DATA_DIR / f"{symbol}_{interval}.parquet")

if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    for s in ["BTCUSDT", "ETHUSDT"]:
        for i in ["1h", "4h"]:
            df = download(s, i); df.to_parquet(DATA_DIR / f"{s}_{i}.parquet")
            gaps = (df.index.to_series().diff() > pd.Timedelta(i)).sum()
            print(s, i, len(df), df.index[0], df.index[-1], "gaps:", gaps)
