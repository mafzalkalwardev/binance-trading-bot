"""Optimize on IS (2020-01..2023-12) over a small grid, report OOS (2024-01..today),
grid-neighborhood stability and a rolling walk-forward (24m train / 6m test)."""
import json, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from tradebot.data import load
from tradebot.strategies import STRATEGIES
from tradebot.backtest import Config, run, buy_hold, metrics

IS = ("2020-01-01", "2023-12-31 23:59")
OOS = ("2024-01-01", None)
SYMBOLS, TFS = ["BTCUSDT", "ETHUSDT"], ["1h", "4h"]
MIN_TRADES = 20

def params_of(s): return {k: v for k, v in s.params().items() if k != "trailing"}

def neighbors(grid, best):
    pb = params_of(best)
    axes = {k: sorted({params_of(g)[k] for g in grid}) for k in pb}
    out = []
    for g in grid:
        p = params_of(g)
        diff = [k for k in pb if p[k] != pb[k]]
        if len(diff) == 1 and abs(axes[diff[0]].index(p[diff[0]]) - axes[diff[0]].index(pb[diff[0]])) == 1:
            out.append(g)
    return out

def pick(rows):
    ok = [r for r in rows if r["m"]["trades"] >= MIN_TRADES] or rows
    return max(ok, key=lambda r: r["m"]["sharpe"])

def walk_forward(df, sigs, grid, cfg, train_m=24, test_m=6):
    t0 = pd.Timestamp("2022-01-01"); end = df.index[-1]; eq_parts, scale, trades, chosen = [], 1.0, [], []
    inpos, notl = [], []
    while t0 < end:
        tr = (t0 - pd.DateOffset(months=train_m), t0 - pd.Timedelta("1s"))
        te = (t0, t0 + pd.DateOffset(months=test_m) - pd.Timedelta("1s"))
        rows = [dict(s=s, m=metrics(run(df, sigs[i], s.stop_mult, s.trailing, cfg, *tr), cfg.initial)) for i, s in enumerate(grid)]
        b = pick(rows)["s"]; chosen.append((str(t0.date()), params_of(b)))
        r = run(df, sigs[grid.index(b)], b.stop_mult, b.trailing, cfg, *te)
        eq_parts.append(r.equity * scale); scale *= r.equity.iloc[-1] / cfg.initial
        trades.append(r.trades); inpos.append(r.in_pos); notl.append(r.notional_frac)
        t0 += pd.DateOffset(months=test_m)
    from tradebot.backtest import Result
    res = Result(pd.concat(eq_parts), pd.concat(trades), pd.concat(inpos), pd.concat(notl))
    return res, chosen

rows, grid_rows, curves = [], [], {}
for sym in SYMBOLS:
    for tf in TFS:
        df = load(sym, tf)
        for per, (a, b) in (("IS", IS), ("OOS", OOS), ("WF", ("2022-01-01", None))):
            m = metrics(buy_hold(df, start=a, end=b))
            rows.append(dict(symbol=sym, tf=tf, strategy="buy_hold", mode="long", period=per, params="", **m))
        curves[(sym, tf, "buy_hold")] = buy_hold(df, start=OOS[0]).equity
        for name, S in STRATEGIES.items():
            grid = list(S.grid()); sigs = [g.signals(df) for g in grid]
            for mode, cfg in (("long", Config()), ("long_short", Config(allow_short=True))):
                ev = []
                for i, g in enumerate(grid):
                    mi = metrics(run(df, sigs[i], g.stop_mult, g.trailing, cfg, *IS))
                    ro = run(df, sigs[i], g.stop_mult, g.trailing, cfg, *OOS); mo = metrics(ro)
                    ev.append(dict(s=g, m=mi, mo=mo, ro=ro))
                    grid_rows.append(dict(symbol=sym, tf=tf, strategy=name, mode=mode, params=json.dumps(params_of(g)),
                                          **{f"is_{k}": v for k, v in mi.items()}, **{f"oos_{k}": v for k, v in mo.items()}))
                best = pick(ev); nb = [e for e in ev if e["s"] in neighbors(grid, best["s"])]
                stab = dict(nbr_is_sharpe_mean=np.mean([e["m"]["sharpe"] for e in nb]) if nb else np.nan,
                            nbr_oos_sharpe_mean=np.mean([e["mo"]["sharpe"] for e in nb]) if nb else np.nan,
                            grid_oos_sharpe_median=np.median([e["mo"]["sharpe"] for e in ev]),
                            grid_oos_frac_positive=np.mean([e["mo"]["total_return"] > 0 for e in ev]))
                p = json.dumps(params_of(best["s"]))
                rows.append(dict(symbol=sym, tf=tf, strategy=name, mode=mode, period="IS", params=p, **best["m"], **stab))
                rows.append(dict(symbol=sym, tf=tf, strategy=name, mode=mode, period="OOS", params=p, **best["mo"], **stab))
                wf, chosen = walk_forward(df, sigs, grid, cfg)
                rows.append(dict(symbol=sym, tf=tf, strategy=name, mode=mode, period="WF", params=json.dumps(chosen),
                                 **metrics(wf)))
                if mode == "long": curves[(sym, tf, name)] = best["ro"].equity
                print(sym, tf, name, mode, p, "OOS sharpe %.2f ret %.1f%%" % (best["mo"]["sharpe"], 100 * best["mo"]["total_return"]), flush=True)

res = pd.DataFrame(rows); res.to_csv("results.csv", index=False)
pd.DataFrame(grid_rows).to_csv("grid_results.csv", index=False)

fig, axs = plt.subplots(2, 2, figsize=(15, 9), sharex=True)
for ax, (sym, tf) in zip(axs.flat, [(s, t) for s in SYMBOLS for t in TFS]):
    for name in ["buy_hold", *STRATEGIES]:
        e = curves[(sym, tf, name)]
        ax.plot(e.index, e.values, lw=1.6 if name == "buy_hold" else 1.1, color="k" if name == "buy_hold" else None,
                label=name if name == "buy_hold" else f"{name} {json.loads(res.query('symbol==@sym and tf==@tf and strategy==@name and mode==\"long\" and period==\"OOS\"').params.iloc[0])}")
    ax.set_title(f"{sym} {tf} — out-of-sample (IS-optimized params), long-only, 1% risk/trade")
    ax.set_yscale("log"); ax.grid(alpha=.3); ax.legend(fontsize=7)
fig.suptitle("Out-of-sample equity 2024-01-01 → %s (start $10k, fees 0.1%%/side + 0.05%% slippage)" % str(pd.Timestamp.now().date()))
fig.tight_layout(); fig.savefig("oos_equity.png", dpi=120)
print("done")
