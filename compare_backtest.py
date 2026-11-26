"""Compare live paper results with backtest expectations.
  python compare_backtest.py [--mode testnet|dryrun]
1) Replays the backtester on MAINNET candles over the bot's live window (same params) and lines up
   trades one by one with the bot's closed trades (entry-time match within one 4h bar).
2) Prints the 2024->today out-of-sample expectations from results.csv (win rate, PF, trades/yr)."""
import argparse, json
import pandas as pd
from paperbot.config import BotCfg
from paperbot.market import MainnetData
from tradebot.strategies import Donchian
from tradebot.backtest import run, Config, metrics

ap = argparse.ArgumentParser(); ap.add_argument("--mode", default="testnet", choices=["testnet", "dryrun"]); a = ap.parse_args()
cfg = BotCfg(); stp = cfg.state_dir / f"state-{a.mode}.json"
if not stp.exists(): raise SystemExit(f"no state file {stp}; has the bot run in {a.mode} mode?")
st = json.loads(stp.read_text()); start = pd.Timestamp(st["started_at"]).tz_localize(None) if pd.Timestamp(st["started_at"]).tzinfo is None else pd.Timestamp(st["started_at"]).tz_convert(None)
closed = pd.DataFrame(st.get("closed", []))
res = pd.read_csv(cfg.state_dir.parent / "results.csv")
md = MainnetData(); days = max((pd.Timestamp.now(tz="UTC").tz_convert(None) - start).total_seconds() / 86400, 1e-9)
print(f"live window: {start} UTC -> now ({days:.1f} days)\n")
for sc in cfg.symbols:
    df = md.closed_candles(sc.symbol, cfg.timeframe, 1000)
    s = Donchian(entry_n=sc.entry_n, exit_n=sc.exit_n, stop_mult=sc.stop_mult)
    bt = run(df, s.signals(df), s.stop_mult, s.trailing, Config(), start=start.floor("4h") + pd.Timedelta("4h"))
    bt_tr = bt.trades
    lv = closed[closed.symbol == sc.symbol] if len(closed) else pd.DataFrame(columns=["entry_time", "exit_time", "entry", "exit", "pnl", "r", "reason"])
    exp = res.query("symbol==@sc.raw and tf=='4h' and strategy=='donchian' and mode=='long' and period=='OOS'").iloc[0]
    print(f"== {sc.symbol} Donchian {sc.entry_n}/{sc.exit_n}, {sc.stop_mult} ATR")
    print(f"  backtest OOS expectation: win {exp.win_rate:.0%}, PF {exp.profit_factor:.2f}, {exp.trades / 2.9:.0f} trades/yr, max DD {exp.max_dd:.1%}")
    print(f"  backtest replay over live window: {len(bt_tr)} trades, return {bt.equity.iloc[-1] / 1e4 - 1:+.2%}" if len(bt.equity) else "  no candles yet")
    lv_ret = lv.pnl.sum() / cfg.capital_cap if len(lv) and cfg.capital_cap else 0.0
    print(f"  live paper: {len(lv)} closed trades, realised P&L {lv.pnl.sum() if len(lv) else 0:+.2f} USDT ({lv_ret:+.2%} of {cfg.capital_cap:.0f}), "
          f"win {(lv.pnl > 0).mean() if len(lv) else float('nan'):.0%}, avg R {lv.r.mean() if len(lv) else float('nan'):.2f}")
    if sc.symbol in st["positions"]: print("  open live position:", st["positions"][sc.symbol])
    rows = []
    for _, t in bt_tr.iterrows():
        m = lv[(pd.to_datetime(lv.entry_time).dt.tz_convert(None) - t.entry_time).abs() <= pd.Timedelta("4h")] if len(lv) else lv
        l = m.iloc[0] if len(m) else None
        rows.append(dict(bt_entry=t.entry_time, bt_exit=t.exit_time, bt_entry_px=round(t.entry, 2), bt_exit_px=round(t.exit, 2), bt_reason=t.reason,
                         live_entry_px=round(l.entry, 2) if l is not None else None, live_exit_px=round(l.exit, 2) if l is not None else None,
                         live_reason=l.reason if l is not None else "MISSING"))
    if rows: print(pd.DataFrame(rows).to_string(index=False))
    print()
