"""Bar-by-bar backtester. Signals at bar i close -> fill at bar i+1 open (no lookahead).
Stops are checked intrabar against high/low; gap-throughs fill at the (worse) open."""
from dataclasses import dataclass, field
import numpy as np, pandas as pd

@dataclass
class Config:
    fee: float = 0.001        # per side
    slippage: float = 0.0005  # per fill, adverse
    risk: float = 0.01        # fraction of equity risked per trade (entry-to-stop)
    max_leverage: float = 1.0 # notional cap as multiple of equity (1.0 = spot, no leverage)
    initial: float = 10_000.0
    allow_short: bool = False

@dataclass
class Result:
    equity: pd.Series
    trades: pd.DataFrame
    in_pos: pd.Series
    notional_frac: pd.Series

def run(df, sig, stop_mult, trailing, cfg=Config(), start=None, end=None):
    """df: OHLC frame; sig: Strategy.signals(df) on the FULL history (indicators warm up before start)."""
    m = slice(start, end)
    d, s = df.loc[m], sig.loc[m]
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close"))
    a = s["atr"].to_numpy()
    el, xl = s.enter_long.to_numpy(bool), s.exit_long.to_numpy(bool)
    es, xs = s.enter_short.to_numpy(bool), s.exit_short.to_numpy(bool)
    n = len(d); fee, slip = cfg.fee, cfg.slippage
    cash, qty = cfg.initial, 0.0
    stop = ext = entry_px = entry_fee = 0.0; entry_t = None
    pending = 0  # +1 open long, -1 open short, 2 close
    pend_atr = 0.0
    eq = np.empty(n); inpos = np.zeros(n, bool); notl = np.zeros(n)
    trades = []

    def close_pos(px, i, reason):
        nonlocal cash, qty
        side = np.sign(qty)
        fill = px * (1 - slip * side)
        f = abs(qty) * fill * fee
        cash += qty * fill - f
        pnl = qty * (fill - entry_px) - entry_fee - f
        trades.append((entry_t, d.index[i], "long" if side > 0 else "short", entry_px, fill, pnl, reason))
        qty = 0.0

    for i in range(n):
        # 1) pending orders at the open
        if pending == 2 and qty != 0:
            close_pos(o[i], i, "signal")
        elif pending in (1, -1) and qty == 0:
            side = pending
            fill = o[i] * (1 + slip * side)
            st = fill - side * stop_mult * pend_atr
            dist = abs(fill - st)
            equity = cash
            q = cfg.risk * equity / dist if dist > 0 else 0.0
            q = min(q, cfg.max_leverage * equity / (fill * (1 + fee)))
            if q > 0:
                entry_fee = q * fill * fee
                cash -= side * q * fill + entry_fee
                qty, entry_px, stop, entry_t = side * q, fill, st, d.index[i]
                ext = h[i] if side > 0 else l[i]
        pending = 0
        # 2) intrabar stop
        if qty > 0 and l[i] <= stop:
            close_pos(min(o[i], stop), i, "stop")
        elif qty < 0 and h[i] >= stop:
            close_pos(max(o[i], stop), i, "stop")
        # 3) mark to market, trail
        eq[i] = cash + qty * c[i]
        if qty != 0:
            inpos[i] = True; notl[i] = abs(qty) * c[i] / eq[i]
            if trailing and not np.isnan(a[i]):
                if qty > 0:
                    ext = max(ext, h[i]); stop = max(stop, ext - stop_mult * a[i])
                else:
                    ext = min(ext, l[i]); stop = min(stop, ext + stop_mult * a[i])
        # 4) signals on this close -> next open
        if i == n - 1 or np.isnan(a[i]):
            continue
        if qty > 0 and xl[i]: pending = 2
        elif qty < 0 and xs[i]: pending = 2
        elif qty == 0:
            if el[i]: pending, pend_atr = 1, a[i]
            elif cfg.allow_short and es[i]: pending, pend_atr = -1, a[i]
        # stop-and-reverse is not done; reversal waits for next entry signal after flat
    if qty != 0:  # close at last close for trade stats (equity already marked)
        close_pos(c[-1], n - 1, "end")
        eq[-1] = cash
    tr = pd.DataFrame(trades, columns=["entry_time", "exit_time", "side", "entry", "exit", "pnl", "reason"])
    return Result(pd.Series(eq, d.index), tr, pd.Series(inpos, d.index), pd.Series(notl, d.index))

def buy_hold(df, cfg=Config(), start=None, end=None):
    d = df.loc[start:end]
    fill = d.open.iloc[0] * (1 + cfg.slippage)
    q = cfg.initial / (fill * (1 + cfg.fee))
    eq = cfg.initial - q * fill * (1 + cfg.fee) + q * d.close
    tr = pd.DataFrame([(d.index[0], d.index[-1], "long", fill, d.close.iloc[-1], eq.iloc[-1] - cfg.initial, "end")],
                      columns=["entry_time", "exit_time", "side", "entry", "exit", "pnl", "reason"])
    return Result(eq, tr, pd.Series(True, d.index), q * d.close / eq)

def metrics(r: Result, initial=10_000.0):
    eq = r.equity
    yrs = (eq.index[-1] - eq.index[0]).total_seconds() / (365.25 * 86400)
    tot = eq.iloc[-1] / initial - 1
    cagr = (eq.iloc[-1] / initial) ** (1 / yrs) - 1 if yrs > 0 and eq.iloc[-1] > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    daily = eq.resample("1D").last().ffill()
    dr = pd.concat([pd.Series([initial], [daily.index[0] - pd.Timedelta("1D")]), daily]).pct_change().dropna()
    sharpe = dr.mean() / dr.std() * np.sqrt(365) if dr.std() > 0 else 0.0
    t = r.trades
    wins, losses = t.pnl[t.pnl > 0].sum(), -t.pnl[t.pnl <= 0].sum()
    return dict(cagr=cagr, total_return=tot, max_dd=dd, sharpe=sharpe,
                calmar=cagr / abs(dd) if dd < 0 else np.nan,
                win_rate=(t.pnl > 0).mean() if len(t) else np.nan,
                profit_factor=wins / losses if losses > 0 else np.nan,
                trades=len(t), exposure=r.in_pos.mean(), avg_notional=r.notional_frac.mean())
