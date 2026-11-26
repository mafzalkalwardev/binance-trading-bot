"""CSV trade journal + rotating log file. A filter redacts the API key/secret if they ever appear."""
import csv, logging, os, datetime as dt
from logging.handlers import RotatingFileHandler
from pathlib import Path

FIELDS = ["ts_utc", "event", "symbol", "side", "qty", "price", "ref_price", "stop", "equity", "pnl", "r_multiple",
          "order_id", "client_id", "details"]

class Redact(logging.Filter):
    def __init__(self):
        super().__init__()
        self.secrets = [v for v in (os.environ.get("BINANCE_TESTNET_API_KEY"), os.environ.get("BINANCE_TESTNET_API_SECRET")) if v]
    def filter(self, rec):
        msg = rec.getMessage()
        for s in self.secrets:
            msg = msg.replace(s, "***")
        rec.msg, rec.args = msg, ()
        return True

def setup_logging(log_dir: Path, name="bot"):
    log_dir.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("paperbot"); lg.setLevel(logging.INFO); lg.handlers.clear()
    fmt = logging.Formatter("%(asctime)s UTC %(levelname)s %(message)s"); fmt.converter = __import__("time").gmtime
    for h in (RotatingFileHandler(log_dir / f"{name}.log", maxBytes=5_000_000, backupCount=5), logging.StreamHandler()):
        h.setFormatter(fmt); h.addFilter(Redact()); lg.addHandler(h)
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    return lg

class Journal:
    def __init__(self, path: Path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with open(self.path, "w", newline="") as f: csv.writer(f).writerow(FIELDS)
    def write(self, event, symbol="", **kw):
        row = {"ts_utc": kw.pop("ts", None) or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "event": event, "symbol": symbol, **kw}
        with open(self.path, "a", newline="") as f:
            csv.DictWriter(f, FIELDS, extrasaction="ignore").writerow(row)
        logging.getLogger("paperbot").info("journal %s", {k: v for k, v in row.items() if v not in (None, "")})
