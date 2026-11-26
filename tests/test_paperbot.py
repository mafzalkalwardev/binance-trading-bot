import datetime as dt, pathlib
import numpy as np, pandas as pd, pytest
from paperbot.config import BotCfg, SymbolCfg
from paperbot.state import State
from paperbot.journal import Journal
from paperbot.broker import SimBroker, TestnetBroker, NotTestnetError
from paperbot.engine import Engine, compute_signal, size_position
from tradebot.strategies import Donchian
from tradebot.backtest import run, Config

FIX = pathlib.Path(__file__).parent / "fixtures" / "ETHUSDT_4h_2023-09_2024-12.csv"
SC = SymbolCfg("ETH/USDT", 55, 50, 2.0)

@pytest.fixture
def df():
    d = pd.read_csv(FIX, index_col=0, parse_dates=True); return d

class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t

def make_engine(tmp_path, prices, usdt=10_000.0, cap=None, clock=None):
    cfg = BotCfg(symbols=[SC], state_dir=tmp_path, log_dir=tmp_path, capital_cap=cap)
    st = State(tmp_path / "s.json"); st["sim"] = st["sim"] or {"balances": {"USDT": usdt}}
    br = SimBroker(st["sim"], lambda s: prices[s], 0.001, 0.0005)
    clock = clock or Clock(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))
    return cfg, st, Engine(cfg, br, st, Journal(tmp_path / "j.csv"), lambda s: prices[s], clock), clock

def test_signal_matches_backtest_without_lookahead(df):
    """Signal computed on history truncated at bar i == full-history signal at bar i."""
    full = Donchian(entry_n=55, exit_n=50, stop_mult=2.0).signals(df)
    idx = [i for i in range(300, len(df)) if full.enter_long.iloc[i] or full.exit_long.iloc[i]][:40] + list(range(300, 340))
    for i in idx:
        s = compute_signal(df.iloc[: i + 1], SC)
        assert s["enter_long"] == bool(full.enter_long.iloc[i]) and s["exit_long"] == bool(full.exit_long.iloc[i])
        assert s["atr"] == pytest.approx(full.atr.iloc[i], rel=1e-9)
        assert s["enter_long"] == (df.close.iloc[i] > df.high.iloc[i - 55:i].max())

def test_sizing_risks_one_percent_and_caps_cash():
    q = size_position(10_000, atr=40, stop_mult=2, price=2000, free_quote=10_000, risk=0.01)
    assert q * 2 * 40 == pytest.approx(100)          # $100 = 1% of equity at risk
    q = size_position(10_000, atr=1, stop_mult=2, price=2000, free_quote=1_000, risk=0.01)
    assert q * 2000 * 1.001 <= 1_000                  # no leverage: capped by free cash
    assert size_position(10_000, atr=0, stop_mult=2, price=2000, free_quote=1e4, risk=0.01) == 0

def test_replay_matches_backtest_trades(tmp_path, df):
    """Drive the live engine bar by bar on recorded candles; entries/exits must match the backtester."""
    prices = {"ETH/USDT": 0.0}
    cfg, st, eng, clock = make_engine(tmp_path, prices)
    start = df.index[300]
    for j in range(301, len(df)):
        clock.t = df.index[j].tz_localize("UTC").to_pydatetime(); o, l = df.open.iloc[j], df.low.iloc[j]
        prices["ETH/USDT"] = o
        if df.index[j - 1] >= start: eng.on_candle("ETH/USDT", df.iloc[:j], o)
        p = st["positions"].get("ETH/USDT")
        if p and l <= p["stop"]:
            px = min(o, p["stop"]); prices["ETH/USDT"] = px; eng.check_stop("ETH/USDT", px)
    sig = Donchian(entry_n=55, exit_n=50, stop_mult=2.0).signals(df)
    bt = run(df, sig, 2.0, False, Config(), start=df.index[301])
    bt = bt.trades[bt.trades.reason != "end"]
    live = pd.DataFrame(st["closed"])
    assert len(live) == len(bt) and len(bt) > 10
    assert list(pd.to_datetime(live.entry_time).dt.tz_localize(None)) == list(bt.entry_time)
    assert list(pd.to_datetime(live.exit_time).dt.tz_localize(None)) == list(bt.exit_time)
    assert np.allclose(live.entry.values, bt.entry.values)

def _force_entry_signal(df):
    for i in range(300, len(df)):
        s = compute_signal(df.iloc[: i + 1], SC)
        if s["enter_long"]: return df.iloc[: i + 1], df.open.iloc[i + 1]

def test_daily_loss_cap_blocks_entries(tmp_path, df):
    hist, px = _force_entry_signal(df); prices = {"ETH/USDT": px}
    cfg, st, eng, clock = make_engine(tmp_path, prices)
    st["day"] = clock().date().isoformat(); st["day_start_equity"] = 10_400  # equity 10,000 is >3% below
    eng.on_candle("ETH/USDT", hist, px)
    assert not st["positions"] and st["paused_day"] == st["day"]

def test_pause_and_kill_switch(tmp_path, df):
    hist, px = _force_entry_signal(df); prices = {"ETH/USDT": px}
    cfg, st, eng, clock = make_engine(tmp_path, prices)
    cfg.pause_file.touch(); eng.on_candle("ETH/USDT", hist, px); assert not st["positions"]
    cfg.pause_file.unlink(); st["last_candle"] = {}
    eng.on_candle("ETH/USDT", hist, px); assert "ETH/USDT" in st["positions"]
    p = st["positions"]["ETH/USDT"]
    assert p["qty"] * (p["entry"] - p["stop"]) == pytest.approx(100, rel=0.01)   # 1% risk
    eng.kill(); assert not st["positions"] and st["halted"]
    eng2_block = eng.risk_gate(10_000); assert eng2_block == "kill switch"

def test_one_position_per_symbol_and_idempotent_candle(tmp_path, df):
    hist, px = _force_entry_signal(df); prices = {"ETH/USDT": px}
    cfg, st, eng, clock = make_engine(tmp_path, prices)
    eng.on_candle("ETH/USDT", hist, px); q = st["positions"]["ETH/USDT"]["qty"]
    st["last_candle"] = {}; eng.on_candle("ETH/USDT", hist, px)       # same entry signal again
    assert st["positions"]["ETH/USDT"]["qty"] == q and len(st["sim"]["orders"]) == 1

def test_restart_recovers_pending_entry_and_detects_missing(tmp_path, df):
    hist, px = _force_entry_signal(df); prices = {"ETH/USDT": px}
    cfg, st, eng, clock = make_engine(tmp_path, prices)
    # simulate crash after the order filled but before state recorded the position
    f = eng.broker.market_buy("ETH/USDT", 0.5, "dceETH1")
    st["pending"]["ETH/USDT"] = dict(kind="entry", client_id="dceETH1", atr=40.0, candle="x"); st.save()
    cfg2, st2, eng2, _ = make_engine(tmp_path, prices)   # reload from disk
    eng2.reconcile()
    assert st2["positions"]["ETH/USDT"]["qty"] == pytest.approx(0.5) and not st2["pending"]
    st2["sim"]["balances"]["ETH"] = 0.0; st2.save()       # e.g. testnet balance reset
    cfg3, st3, eng3, _ = make_engine(tmp_path, prices); eng3.reconcile()
    assert not st3["positions"]

def test_testnet_guard_refuses_mainnet(monkeypatch):
    import ccxt
    monkeypatch.setattr(ccxt.binance, "set_sandbox_mode", lambda self, enabled: None)  # sandbox "forgotten"
    with pytest.raises(NotTestnetError):
        TestnetBroker("dummy", "dummy")

def test_testnet_broker_requires_keys(monkeypatch):
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False); monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)
    with pytest.raises(RuntimeError): TestnetBroker()

def test_redaction(monkeypatch, tmp_path):
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "SECRETKEY123"); monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "SECRETSEC456")
    from paperbot.journal import setup_logging
    lg = setup_logging(tmp_path, "t"); lg.info("oops SECRETKEY123 and SECRETSEC456")
    for h in lg.handlers: h.flush()
    txt = (tmp_path / "t.log").read_text(); assert "SECRET" not in txt and "***" in txt
