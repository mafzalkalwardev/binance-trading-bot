"""Signal data: Binance MAINNET public candles (no keys). Testnet candles track mainnet closes
but carry fake wicks (seen up to ~35% on 4h highs/lows), which would corrupt Donchian highs/lows
and trigger stops spuriously, so signals and stop checks use the real market."""
import time, datetime as dt
import ccxt, pandas as pd

TF_MS = {"1h": 3_600_000, "4h": 14_400_000}

class MainnetData:
    def __init__(self):
        self.ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot", "fetchMarkets": ["spot"]}})

    def closed_candles(self, symbol, tf="4h", limit=1000, now_ms=None):
        now_ms = now_ms or int(time.time() * 1000)
        rows = self.ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
        df = df[df.t + TF_MS[tf] <= now_ms]          # only fully closed candles
        df.index = pd.to_datetime(df.pop("t"), unit="ms")
        return df

    def last_price(self, symbol):
        return float(self.ex.fetch_ticker(symbol)["last"])

def next_close(now: dt.datetime, tf="4h") -> dt.datetime:
    step = TF_MS[tf] // 1000
    ts = int(now.timestamp()); return dt.datetime.fromtimestamp((ts // step + 1) * step, dt.timezone.utc)
