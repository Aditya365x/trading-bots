"""
Thin wrapper around Delta Exchange's REST API for the testnet demo bot.

Design goals:
  - Keep ALL exchange-specific calls in this one file, so when an API
    signature differs from what we assume, there's a single place to fix.
  - dry_run guard on every state-changing call (no accidental live orders).
  - Public market data (products, candles) via plain requests; private calls
    (balance, positions, orders) via the official delta-rest-client (it signs).

VERIFY against your testnet account / Delta API docs:
  - base_url host for India testnet
  - product symbol + product_id + contract_value (lot size)
  - place_order / stop-order parameter names
Run test_connection.py FIRST — it exercises only the read-only calls.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

try:
    from delta_rest_client import DeltaRestClient, OrderType
except Exception:                      # library not installed yet
    DeltaRestClient = None
    OrderType = None


class DeltaBroker:
    def __init__(self, base_url: str, api_key: str, api_secret: str, dry_run: bool = True):
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self._product_cache: dict = {}
        if DeltaRestClient is None:
            raise RuntimeError("delta-rest-client not installed. pip install -r requirements.txt")
        # private/authenticated client (signs requests with your key/secret)
        self.client = DeltaRestClient(base_url=self.base_url, api_key=api_key, api_secret=api_secret)

    # ---------------- public market data ----------------
    def get_product(self, symbol: str) -> dict:
        """Resolve a symbol to its product dict (id, contract_value, tick_size)."""
        if symbol in self._product_cache:
            return self._product_cache[symbol]
        r = requests.get(f"{self.base_url}/v2/products", timeout=15)
        r.raise_for_status()
        for p in r.json().get("result", []):
            if p.get("symbol") == symbol:
                self._product_cache[symbol] = p
                return p
        raise ValueError(f"Symbol {symbol!r} not found on {self.base_url}")

    def get_candles(self, symbol: str, resolution: str = "5m", count: int = 500) -> list[dict]:
        """OHLCV history. Delta resolutions: 1m,5m,15m,1h,... Returns oldest->newest."""
        end = int(time.time())
        # 5m * count seconds back (pad a bit)
        secs = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
                "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400}.get(resolution, 300)
        start = end - secs * (count + 5)
        params = {"resolution": resolution, "symbol": symbol, "start": start, "end": end}
        r = requests.get(f"{self.base_url}/v2/history/candles", params=params, timeout=20)
        r.raise_for_status()
        rows = r.json().get("result", [])
        rows = sorted(rows, key=lambda x: x["time"])
        return rows

    # ---------------- private (auth) ----------------
    def settling_asset_id(self, symbol: str) -> int:
        """The wallet/settlement asset id for a product (e.g. USD = 3 on testnet)."""
        p = self.get_product(symbol)
        return int((p.get("settling_asset") or {}).get("id", 0))

    def get_balance(self, asset_id: int) -> Optional[dict]:
        try:
            return self.client.get_balances(asset_id)
        except Exception as e:
            print(f"[balance] {type(e).__name__}: {e}")
            return None

    def get_position(self, product_id: int) -> Optional[dict]:
        try:
            return self.client.get_position(product_id=product_id)
        except Exception as e:
            print(f"[position] {type(e).__name__}: {e}")
            return None

    # ---------------- orders (guarded by dry_run) ----------------
    def market_order(self, product_id: int, size: int, side: str, reduce_only: bool = False):
        """side = 'buy' | 'sell'. size = whole contracts."""
        if size <= 0:
            print(f"[order] skipped, size={size}")
            return None
        if self.dry_run:
            print(f"[DRY-RUN] MARKET {side} {size} contracts product={product_id} reduce_only={reduce_only}")
            return {"dry_run": True, "side": side, "size": size}
        return self.client.place_order(product_id=product_id, size=size, side=side,
                                       order_type=OrderType.MARKET,
                                       reduce_only="true" if reduce_only else "false")

    def stop_order(self, product_id: int, size: int, side: str, stop_price: float):
        """Resting stop-market on the EXCHANGE (this is the real protection, not virtual)."""
        if self.dry_run:
            print(f"[DRY-RUN] STOP {side} {size} @ {stop_price} product={product_id}")
            return {"dry_run": True}
        # NOTE: verify stop-order params against delta-rest-client / docs.
        return self.client.place_stop_order(product_id=product_id, size=size, side=side,
                                            stop_price=str(stop_price), order_type=OrderType.MARKET)

    def cancel_all(self, product_id: int):
        if self.dry_run:
            print(f"[DRY-RUN] cancel all orders product={product_id}")
            return None
        # NOTE: cancel_all_orders() does NOT remove stop/conditional orders on Delta.
        # Cancel each live order by id so stops are actually cleared (no orphans).
        cancelled = 0
        try:
            for o in (self.client.get_live_orders() or []):
                if int(o.get("product_id", -1)) == product_id:
                    self.client.cancel_order(product_id=product_id, order_id=o["id"])
                    cancelled += 1
        except Exception as e:
            print(f"[cancel_all] {type(e).__name__}: {e}")
        return cancelled

    def close_position(self, product_id: int, position: dict):
        """Flatten by sending an opposing market order for the current size."""
        size = abs(int(position.get("size", 0)))
        if size == 0:
            return None
        side = "sell" if int(position["size"]) > 0 else "buy"
        return self.market_order(product_id, size, side, reduce_only=True)
