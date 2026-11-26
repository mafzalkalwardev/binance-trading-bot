"""Rule-based strategies. Each returns a Signals frame computed only from data up to and
including each bar's close (causal). The backtester/live bot acts on the NEXT bar's open."""
from dataclasses import dataclass, asdict
import itertools
import numpy as np, pandas as pd
from .indicators import ema, atr, rsi

SIG_COLS = ["enter_long", "exit_long", "enter_short", "exit_short", "atr"]

@dataclass
class Strategy:
    stop_mult: float = 3.0
    trailing: bool = True
    name = "base"
    def params(self): return asdict(self)
    def signals(self, df) -> pd.DataFrame: raise NotImplementedError
    def label(self): return f"{self.name}(" + ",".join(f"{k}={v}" for k, v in self.params().items()) + ")"

def _cross_up(a, b): return (a > b) & (a.shift() <= b.shift())

@dataclass
class EmaCross(Strategy):
    fast: int = 20
    slow: int = 100
    stop_mult: float = 3.0
    trailing: bool = True
    name = "ema_cross"
    def signals(self, df):
        f, s = ema(df.close, self.fast), ema(df.close, self.slow)
        up, dn = _cross_up(f, s), _cross_up(s, f)
        return pd.DataFrame(dict(enter_long=up, exit_long=f < s, enter_short=dn, exit_short=f > s,
                                 atr=atr(df)), index=df.index)
    @staticmethod
    def grid():
        for f, s, m in itertools.product([10, 20, 50], [50, 100, 200], [2.0, 3.0, 4.0]):
            if f < s: yield EmaCross(fast=f, slow=s, stop_mult=m)

@dataclass
class Donchian(Strategy):
    entry_n: int = 55
    exit_n: int = 20
    stop_mult: float = 2.0
    trailing: bool = False   # turtle: fixed initial 2N stop, channel exit does the trailing
    name = "donchian"
    def signals(self, df):
        hh = df.high.rolling(self.entry_n).max().shift()
        ll = df.low.rolling(self.entry_n).min().shift()
        xl = df.low.rolling(self.exit_n).min().shift()
        xh = df.high.rolling(self.exit_n).max().shift()
        return pd.DataFrame(dict(enter_long=df.close > hh, exit_long=df.close < xl,
                                 enter_short=df.close < ll, exit_short=df.close > xh,
                                 atr=atr(df)), index=df.index)
    @staticmethod
    def grid():
        for n, x, m in itertools.product([20, 55, 100], [10, 20, 50], [2.0, 3.0]):
            if x < n: yield Donchian(entry_n=n, exit_n=x, stop_mult=m)

@dataclass
class RsiReversion(Strategy):
    rsi_n: int = 14
    entry: float = 30
    exit: float = 55
    trend_n: int = 200
    stop_mult: float = 2.0
    trailing: bool = False
    name = "rsi_mr"
    def signals(self, df):
        r, t = rsi(df.close, self.rsi_n), ema(df.close, self.trend_n)
        return pd.DataFrame(dict(enter_long=(r < self.entry) & (df.close > t), exit_long=r > self.exit,
                                 enter_short=(r > 100 - self.entry) & (df.close < t), exit_short=r < 100 - self.exit,
                                 atr=atr(df)), index=df.index)
    @staticmethod
    def grid():
        for n, e, x, m in itertools.product([7, 14], [25, 30, 35], [50, 65], [2.0, 3.0]):
            yield RsiReversion(rsi_n=n, entry=e, exit=x, stop_mult=m)

STRATEGIES = {"ema_cross": EmaCross, "donchian": Donchian, "rsi_mr": RsiReversion}
