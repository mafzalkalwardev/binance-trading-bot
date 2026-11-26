"""Bot configuration. Params come from the backtest (IS-optimised, OOS-validated)."""
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

@dataclass
class SymbolCfg:
    symbol: str        # ccxt symbol, e.g. "ETH/USDT"
    entry_n: int
    exit_n: int
    stop_mult: float = 2.0

    @property
    def base(self): return self.symbol.split("/")[0]
    @property
    def raw(self): return self.symbol.replace("/", "")

@dataclass
class BotCfg:
    symbols: list = field(default_factory=lambda: [
        SymbolCfg("ETH/USDT", 55, 50, 2.0),
        SymbolCfg("BTC/USDT", 100, 20, 2.0),
    ])
    timeframe: str = "4h"
    quote: str = "USDT"
    risk_per_trade: float = 0.01      # fraction of bot equity risked entry->stop
    daily_loss_cap: float = 0.03      # pause new entries for the rest of the UTC day
    capital_cap: float | None = 10_000.0  # size as if the account had at most this much (testnet balances are huge/arbitrary)
    candle_delay_s: int = 60          # wake this long after each 4h close
    poll_s: int = 60                  # stop-monitor cadence
    history: int = 1000               # closed candles fetched for indicators
    sim_fee: float = 0.001            # dry-run broker fee/side
    sim_slippage: float = 0.0005      # dry-run broker slippage
    sim_start_usdt: float = 10_000.0
    state_dir: Path = ROOT / "state"
    log_dir: Path = ROOT / "logs"

    @property
    def kill_file(self): return self.state_dir / "KILL"     # flatten everything and stop
    @property
    def pause_file(self): return self.state_dir / "PAUSE"   # no new entries, keep managing stops/exits
