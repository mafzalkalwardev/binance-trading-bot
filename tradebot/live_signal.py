"""Dry-run: fetch recent CLOSED candles from Binance public REST and print the strategy's
action for the next open. No API key, no orders. Next step: swap print() for ccxt testnet orders
(ccxt.binance({'options': {'defaultType': 'spot'}}).set_sandbox_mode(True)) and persist position state."""
import sys, pandas as pd, requests
from .strategies import Donchian

def recent(symbol="ETHUSDT", interval="4h", limit=500):
    rows = requests.get("https://api.binance.com/api/v3/klines",
                        params=dict(symbol=symbol, interval=interval, limit=limit), timeout=30).json()
    df = pd.DataFrame(rows).iloc[:, :6].astype(float); df.columns = ["t", "open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df.pop("t").astype("int64"), unit="ms")
    return df.iloc[:-1]  # drop the still-forming candle

if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
    s = Donchian(entry_n=55, exit_n=50, stop_mult=2.0)
    df = recent(sym); sig = s.signals(df).iloc[-1]
    print(f"{sym} last closed 4h bar {df.index[-1]} UTC close={df.close.iloc[-1]:.2f} ATR={sig.atr:.2f}")
    print("enter_long" if sig.enter_long else "exit_long" if sig.exit_long else "no new signal",
          f"| initial stop if entering: {df.close.iloc[-1] - s.stop_mult * sig.atr:.2f}")
