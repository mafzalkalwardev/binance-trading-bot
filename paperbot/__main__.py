"""CLI:
  python -m paperbot run   [--dry-run]   24/7 loop (wakes ~60s after each 4h close; polls stops every 60s)
  python -m paperbot once  [--dry-run]   reconcile + process the latest closed candle once, then exit
  python -m paperbot status [--dry-run]  show state/equity
  python -m paperbot smoke               testnet smoke test: auth, balances, signals; trades only if a real signal exists
Dry run uses a simulated broker (no keys) with separate state/journal files."""
import argparse, sys, time, datetime as dt, logging
from .config import BotCfg
from .state import State
from .journal import Journal, setup_logging
from .market import MainnetData, next_close
from .broker import SimBroker, TestnetBroker
from .engine import Engine, utcnow

def build(dry, cfg=None):
    cfg = cfg or BotCfg(); tag = "dryrun" if dry else "testnet"
    log = setup_logging(cfg.log_dir, f"bot-{tag}")
    st = State(cfg.state_dir / f"state-{tag}.json"); j = Journal(cfg.log_dir / f"journal-{tag}.csv")
    md = MainnetData()
    if dry:
        if not st["sim"]: st["sim"] = {"balances": {cfg.quote: cfg.sim_start_usdt}}
        br = SimBroker(st["sim"], md.last_price, cfg.sim_fee, cfg.sim_slippage)
    else:
        br = TestnetBroker()
    return cfg, log, st, j, md, Engine(cfg, br, st, j, md.last_price)

def step_all(cfg, md, eng, log):
    prices = {s.symbol: md.last_price(s.symbol) for s in cfg.symbols}
    eng.risk_gate(eng.equity(prices))  # roll UTC day / daily cap bookkeeping
    out = {}
    for sc in cfg.symbols:
        df = md.closed_candles(sc.symbol, cfg.timeframe, cfg.history)
        out[sc.symbol] = eng.on_candle(sc.symbol, df, prices[sc.symbol])
    return out

def cmd_run(dry):
    cfg, log, st, j, md, eng = build(dry)
    j.write("start", details=f"mode={'dryrun' if dry else 'testnet'} broker={eng.broker.name}")
    eng.reconcile()
    step_all(cfg, md, eng, log)   # catch up on the latest closed candle (idempotent per candle)
    nxt = next_close(utcnow(), cfg.timeframe)
    while True:
        try:
            if cfg.kill_file.exists() and not st["halted"]:
                eng.kill(); log.warning("kill switch engaged; exiting loop"); return
            if st["halted"]:
                log.warning("bot halted; exiting"); return
            for sym in list(st["positions"]):
                eng.check_stop(sym, md.last_price(sym))
            if utcnow() >= nxt + dt.timedelta(seconds=cfg.candle_delay_s):
                step_all(cfg, md, eng, log); nxt = next_close(utcnow(), cfg.timeframe)
                log.info("next candle close %s UTC", nxt.isoformat())
        except Exception as e:   # network hiccups etc.: log and keep going
            log.exception("loop error: %s", type(e).__name__)
        time.sleep(cfg.poll_s)

def cmd_once(dry):
    cfg, log, st, j, md, eng = build(dry)
    eng.reconcile()
    for sym, s in step_all(cfg, md, eng, log).items():
        print(sym, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()})
    cmd_status(dry, (cfg, log, st, j, md, eng))

def cmd_status(dry, built=None):
    cfg, log, st, j, md, eng = built or build(dry)
    print("broker:", eng.broker.name, "| equity (sizing basis): %.2f" % eng.equity())
    print("positions:", st["positions"] or "none"); print("halted:", st["halted"], "| paused_day:", st["paused_day"],
          "| kill file:", cfg.kill_file.exists(), "| pause file:", cfg.pause_file.exists())

def cmd_smoke():
    cfg, log, st, j, md, eng = build(False)
    eng.broker.assert_testnet()
    bal = eng.broker.balances()
    print("testnet auth OK; non-zero balances:", {k: bal[k] for k in sorted(bal) if k in (cfg.quote, "BTC", "ETH")}, f"(+{len(bal)} assets total)")
    eng.reconcile()
    for sym, s in step_all(cfg, md, eng, log).items():
        print(sym, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()})
    cmd_status(False, (cfg, log, st, j, md, eng))

def main(argv=None):
    ap = argparse.ArgumentParser(prog="paperbot"); ap.add_argument("cmd", choices=["run", "once", "status", "smoke"])
    ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args(argv)
    if a.cmd == "run": cmd_run(a.dry_run)
    elif a.cmd == "once": cmd_once(a.dry_run)
    elif a.cmd == "status": cmd_status(a.dry_run)
    else:
        try: cmd_smoke()
        except RuntimeError as e:
            print("smoke test not run:", e); sys.exit(2)

if __name__ == "__main__":
    main()
