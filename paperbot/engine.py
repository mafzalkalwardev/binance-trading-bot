"""Trading engine: one Donchian long-only position per symbol, fixed 2 ATR stop tracked by the bot,
1% risk sizing, daily loss cap, pause/kill files, restart-safe state with exchange reconciliation."""
import logging, datetime as dt
import pandas as pd
from tradebot.strategies import Donchian

log = logging.getLogger("paperbot")
utcnow = lambda: dt.datetime.now(dt.timezone.utc)

def size_position(equity, atr, stop_mult, price, free_quote, risk, fee=0.001):
    """Units so that (entry - stop) * qty = risk * equity, capped by free quote cash (no leverage)."""
    dist = stop_mult * atr
    if dist <= 0 or price <= 0 or equity <= 0: return 0.0
    qty = risk * equity / dist
    return max(0.0, min(qty, free_quote * 0.995 / (price * (1 + fee))))

def compute_signal(df_closed, sc):
    """Signal on the LAST closed candle. Same code path as the backtest (tradebot.strategies.Donchian)."""
    s = Donchian(entry_n=sc.entry_n, exit_n=sc.exit_n, stop_mult=sc.stop_mult).signals(df_closed).iloc[-1]
    return dict(candle=df_closed.index[-1], close=float(df_closed.close.iloc[-1]), atr=float(s.atr),
                enter_long=bool(s.enter_long), exit_long=bool(s.exit_long),
                entry_level=float(df_closed.high.iloc[-sc.entry_n - 1:-1].max()),
                exit_level=float(df_closed.low.iloc[-sc.exit_n - 1:-1].min()))

class Engine:
    def __init__(self, cfg, broker, state, journal, price_fn, clock=utcnow):
        self.cfg, self.broker, self.st, self.j, self.price_fn, self.clock = cfg, broker, state, journal, price_fn, clock
        self.by_sym = {s.symbol: s for s in cfg.symbols}
        if not self.st["started_at"]:
            self.st["started_at"] = self.clock().isoformat(); self.st.save()

    # ---------- equity / risk ----------
    def equity(self, prices=None):
        bal = self.broker.balances(); prices = prices or {}
        eq = bal.get(self.cfg.quote, 0.0)
        for sym, p in self.st["positions"].items():
            eq += p["qty"] * (prices.get(sym) or self.price_fn(sym))
        if self.cfg.capital_cap:
            # size as if the account were capital_cap + bot P&L (testnet balances are arbitrary)
            pnl = sum(p.get("pnl", 0) for p in self.st.data.setdefault("closed", []))
            open_pnl = sum(p["qty"] * ((prices.get(s) or self.price_fn(s)) - p["entry"]) for s, p in self.st["positions"].items())
            eq = min(eq, self.cfg.capital_cap + pnl + open_pnl)
        return eq

    def risk_gate(self, eq):
        """Returns reason string if new entries are blocked, else None. Rolls the UTC day."""
        today = self.clock().date().isoformat()
        if self.st["day"] != today:
            self.st["day"], self.st["day_start_equity"], self.st["paused_day"] = today, eq, None
            self.st.save()
        if self.cfg.kill_file.exists() or self.st["halted"]: return "kill switch"
        if self.cfg.pause_file.exists(): return "pause file"
        if self.st["paused_day"] == today: return "daily loss cap"
        start = self.st["day_start_equity"] or eq
        if eq <= start * (1 - self.cfg.daily_loss_cap):
            self.st["paused_day"] = today; self.st.save()
            self.j.write("risk", details=f"daily loss cap hit: equity {eq:.2f} <= {start:.2f}*(1-{self.cfg.daily_loss_cap})", equity=round(eq, 2))
            return "daily loss cap"
        return None

    # ---------- startup ----------
    def reconcile(self):
        """Resolve orders that were in flight at shutdown, then check tracked positions against balances."""
        for sym, pend in list(self.st["pending"].items()):
            f = self.broker.order_by_client_id(sym, pend["client_id"])
            if pend["kind"] == "entry":
                if f and f.qty > 0: self._open_from_fill(sym, f, pend["atr"], pend["candle"], note="recovered on restart")
                else: self.j.write("reconcile", sym, details="pending entry not found/unfilled; dropped", client_id=pend["client_id"])
            else:
                if f and f.qty > 0: self._close_from_fill(sym, f, pend["reason"], note="recovered on restart")
                else: self.j.write("reconcile", sym, details="pending exit not filled; position kept", client_id=pend["client_id"])
            self.st["pending"].pop(sym, None); self.st.save()
        bal = self.broker.balances()
        for sym, p in list(self.st["positions"].items()):
            have = bal.get(sym.split("/")[0], 0.0)
            if have < p["qty"] * 0.98:
                self.j.write("reconcile", sym, qty=p["qty"], details=f"exchange holds {have} < tracked {p['qty']}; marking position closed (testnet reset or manual sell)")
                self.st["positions"].pop(sym); self.st.save()
            else:
                self.j.write("reconcile", sym, qty=p["qty"], stop=p["stop"], details=f"position confirmed (exchange {have})")
        if not self.st["positions"]: self.j.write("reconcile", details="no open positions")

    # ---------- orders ----------
    def _cid(self, sym, kind, ts):
        return f"dc{kind[0]}{sym.split('/')[0]}{int(pd.Timestamp(ts).timestamp())}"[:36]

    def _open_from_fill(self, sym, f, atr, candle, note=""):
        sc = self.by_sym[sym]; stop = f.price - sc.stop_mult * atr
        self.st["positions"][sym] = dict(qty=f.qty, entry=f.price, stop=stop, atr=atr, entry_time=self.clock().isoformat(),
                                         candle=str(candle), entry_fee=f.fee_quote, client_id=f.client_id, order_id=f.order_id,
                                         risk_quote=f.qty * (f.price - stop))
        self.st.save()
        self.j.write("fill", sym, side="buy", qty=f.qty, price=f.price, stop=round(stop, 2), order_id=f.order_id,
                     client_id=f.client_id, details=f"entry fee~{f.fee_quote:.4f} {note}".strip())

    def _close_from_fill(self, sym, f, reason, note=""):
        p = self.st["positions"].pop(sym)
        pnl = f.qty * (f.price - p["entry"]) - p.get("entry_fee", 0) - f.fee_quote
        r = pnl / p["risk_quote"] if p.get("risk_quote") else None
        self.st.data.setdefault("closed", []).append(dict(symbol=sym, entry_time=p["entry_time"], exit_time=self.clock().isoformat(),
                                                          entry=p["entry"], exit=f.price, qty=f.qty, pnl=pnl, r=r, reason=reason))
        self.st.save()
        self.j.write("fill", sym, side="sell", qty=f.qty, price=f.price, pnl=round(pnl, 4), r_multiple=round(r, 3) if r is not None else None,
                     order_id=f.order_id, client_id=f.client_id, details=f"exit:{reason} {note}".strip())

    def enter(self, sym, sig, price, eq):
        sc = self.by_sym[sym]; bal = self.broker.balances()
        qty = size_position(eq, sig["atr"], sc.stop_mult, price, bal.get(self.cfg.quote, 0.0), self.cfg.risk_per_trade)
        qty = self.broker.amount_to_precision(sym, qty)
        if qty * price < self.broker.min_notional(sym):
            self.j.write("skip", sym, details=f"size {qty} below min notional", equity=round(eq, 2)); return
        cid = self._cid(sym, "entry", sig["candle"])
        self.st["pending"][sym] = dict(kind="entry", client_id=cid, atr=sig["atr"], candle=str(sig["candle"])); self.st.save()
        self.j.write("order", sym, side="buy", qty=qty, ref_price=price, equity=round(eq, 2), client_id=cid,
                     stop=round(price - sc.stop_mult * sig["atr"], 2), details="market entry")
        try:
            f = self.broker.market_buy(sym, qty, cid, price=price)
        except Exception as e:
            self.st["pending"].pop(sym, None); self.st.save()
            self.j.write("error", sym, side="buy", details=f"entry failed: {type(e).__name__}: {e}"); return
        self.st["pending"].pop(sym, None)
        self._open_from_fill(sym, f, sig["atr"], sig["candle"])

    def exit(self, sym, price, reason):
        p = self.st["positions"][sym]; base = sym.split("/")[0]
        qty = self.broker.amount_to_precision(sym, min(p["qty"], self.broker.balances().get(base, 0.0)))
        cid = self._cid(sym, "xit", self.clock())
        self.st["pending"][sym] = dict(kind="exit", client_id=cid, reason=reason); self.st.save()
        self.j.write("order", sym, side="sell", qty=qty, ref_price=price, stop=round(p["stop"], 2), client_id=cid, details=f"market exit:{reason}")
        try:
            f = self.broker.market_sell(sym, qty, cid, price=price)
        except Exception as e:
            self.st["pending"].pop(sym, None); self.st.save()
            self.j.write("error", sym, side="sell", details=f"exit failed: {type(e).__name__}: {e}"); return
        self.st["pending"].pop(sym, None)
        self._close_from_fill(sym, f, reason)

    # ---------- per-candle and per-poll logic ----------
    def on_candle(self, sym, df_closed, price):
        sc = self.by_sym[sym]; sig = compute_signal(df_closed, sc); key = str(sig["candle"])
        if self.st["last_candle"].get(sym) == key: return sig
        pos = self.st["positions"].get(sym)
        action = ("exit" if sig["exit_long"] else "hold") if pos else ("enter" if sig["enter_long"] else "flat")
        self.j.write("signal", sym, ref_price=price, stop=round(pos["stop"], 2) if pos else None,
                     details=f"candle={key} close={sig['close']:.2f} atr={sig['atr']:.2f} entry_lvl={sig['entry_level']:.2f} "
                             f"exit_lvl={sig['exit_level']:.2f} action={action}")
        if action == "exit":
            self.exit(sym, price, "donchian_exit")
        elif action == "enter":
            eq = self.equity({sym: price}); block = self.risk_gate(eq)
            if block: self.j.write("skip", sym, details=f"entry blocked: {block}", equity=round(eq, 2))
            else: self.enter(sym, sig, price, eq)
        self.st["last_candle"][sym] = key; self.st.save()
        return sig

    def check_stop(self, sym, price):
        p = self.st["positions"].get(sym)
        if p and price <= p["stop"]:
            self.j.write("stop", sym, ref_price=price, stop=round(p["stop"], 2), details="stop touched")
            self.exit(sym, price, "stop")

    def kill(self):
        for sym in list(self.st["positions"]):
            self.exit(sym, self.price_fn(sym), "kill_switch")
        self.st["halted"] = True; self.st.save()
        self.j.write("risk", details="kill switch: flattened and halted (delete state/KILL and set halted=false in state to resume)")
