"""Brokers: SimBroker (dry run, no keys) and TestnetBroker (Binance Spot Testnet via ccxt).
TestnetBroker refuses to exist unless ccxt is in sandbox mode and every endpoint is testnet.binance.vision."""
import os, math
from dataclasses import dataclass
import ccxt

TESTNET_HOST = "https://testnet.binance.vision/"

class NotTestnetError(RuntimeError): pass

@dataclass
class Fill:
    order_id: str
    client_id: str
    qty: float          # base quantity actually credited/debited (net of base-asset fees)
    price: float        # average fill price
    fee_quote: float    # fee expressed in quote currency (approx.)
    status: str = "closed"

class SimBroker:
    """In-memory paper broker; its balances persist in the bot state under 'sim'."""
    name = "sim"
    def __init__(self, data, price_fn, fee=0.001, slippage=0.0005, markets=None):
        self.d, self.price_fn, self.fee, self.slip = data, price_fn, fee, slippage
        self.orders = data.setdefault("orders", {})
    def balances(self): return dict(self.d["balances"])
    def amount_to_precision(self, symbol, qty): return math.floor(qty * 1e5) / 1e5
    def min_notional(self, symbol): return 5.0
    def _fill(self, symbol, side, qty, cid, price=None):
        base, quote = symbol.split("/"); b = self.d["balances"]
        px = (price or self.price_fn(symbol)) * (1 + self.slip if side == "buy" else 1 - self.slip)
        if side == "buy":
            cost = qty * px; fee = cost * self.fee
            if cost + fee > b.get(quote, 0) + 1e-9: raise ccxt.InsufficientFunds("sim: insufficient quote")
            b[quote] -= cost + fee; b[base] = b.get(base, 0) + qty
        else:
            if qty > b.get(base, 0) + 1e-12: raise ccxt.InsufficientFunds("sim: insufficient base")
            fee = qty * px * self.fee; b[base] -= qty; b[quote] = b.get(quote, 0) + qty * px - fee
        f = Fill(f"sim-{len(self.orders)+1}", cid, qty, px, fee)
        self.orders[cid] = f.__dict__; return f
    def market_buy(self, symbol, qty, cid, price=None): return self._fill(symbol, "buy", qty, cid, price)
    def market_sell(self, symbol, qty, cid, price=None): return self._fill(symbol, "sell", qty, cid, price)
    def order_by_client_id(self, symbol, cid):
        o = self.orders.get(cid); return Fill(**o) if o else None

class TestnetBroker:
    __test__ = False
    name = "testnet"
    def __init__(self, api_key=None, secret=None):
        api_key = api_key or os.environ.get("BINANCE_TESTNET_API_KEY")
        secret = secret or os.environ.get("BINANCE_TESTNET_API_SECRET")
        if not api_key or not secret:
            raise RuntimeError("BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET not set (use --dry-run)")
        self.ex = ccxt.binance({"apiKey": api_key, "secret": secret, "enableRateLimit": True,
                                "options": {"defaultType": "spot", "fetchMarkets": ["spot"], "warnOnFetchOpenOrdersWithoutSymbol": False}})
        self.ex.set_sandbox_mode(True)
        self.assert_testnet()
        self.ex.load_markets()

    def assert_testnet(self):
        """Hard guard: there is deliberately no flag to disable this."""
        if not getattr(self.ex, "isSandboxModeEnabled", False):
            raise NotTestnetError("sandbox mode is not enabled")
        api = self.ex.urls.get("api", {})
        for k in ("public", "private"):
            if not str(api.get(k, "")).startswith(TESTNET_HOST):
                raise NotTestnetError(f"{k} endpoint is not the Binance Spot Testnet")

    def balances(self):
        self.assert_testnet()
        bal = self.ex.fetch_balance()
        return {k: float(v) for k, v in bal["total"].items() if v}

    def amount_to_precision(self, symbol, qty):
        return float(self.ex.amount_to_precision(symbol, qty))   # ccxt truncates by default

    def min_notional(self, symbol):
        lim = self.ex.market(symbol)["limits"]["cost"]["min"]; return float(lim or 5.0)

    def _to_fill(self, symbol, o, cid):
        base = symbol.split("/")[0]; filled = float(o.get("filled") or 0); avg = float(o.get("average") or o.get("price") or 0)
        base_fee = quote_fee = 0.0
        for f in o.get("fees") or ([o["fee"]] if o.get("fee") else []):
            if not f or f.get("cost") is None: continue
            if f.get("currency") == base: base_fee += float(f["cost"])
            elif f.get("currency") == symbol.split("/")[1]: quote_fee += float(f["cost"])
        side = o.get("side")
        net = filled - base_fee if side == "buy" else filled
        return Fill(str(o["id"]), cid, net, avg, quote_fee + base_fee * avg, o.get("status") or "")

    def _market(self, symbol, side, qty, cid):
        self.assert_testnet()
        o = self.ex.create_order(symbol, "market", side, qty, None, {"newClientOrderId": cid, "newOrderRespType": "FULL"})
        return self._to_fill(symbol, o, cid)

    def market_buy(self, symbol, qty, cid, price=None): return self._market(symbol, "buy", qty, cid)
    def market_sell(self, symbol, qty, cid, price=None): return self._market(symbol, "sell", qty, cid)

    def order_by_client_id(self, symbol, cid):
        self.assert_testnet()
        try:
            o = self.ex.fetch_order(None, symbol, {"origClientOrderId": cid})
        except ccxt.OrderNotFound:
            return None
        return self._to_fill(symbol, o, cid)
