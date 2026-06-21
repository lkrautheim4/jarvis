#!/usr/bin/env python3
"""
Read-only cross-bot portfolio aggregator (execution coordination, item #2).

Each Jarvis trading bot tracks only its OWN positions, so no bot can see total
account exposure or concentration — that's how you over-concentrate without
knowing it. This module reads the live truth from every domain and returns ONE
consolidated view:
  - Alpaca positions (stocks + crypto) — the shared paper account
  - Options open book — paper_trades_store.py
  - Kalshi open bets — kalshi_brain.json (via jarvis_position_monitor)

FIRST PASS = AWARENESS ONLY. Nothing here blocks a trade; it exposes exposure
and concentration so bots can log warnings and the brief can show the whole
picture. A hard global ceiling can be layered on top once this is trusted.

Fail-open on API error (empty/partial view) — a transient Alpaca outage should
not wedge trading; the picture refills as soon as the API recovers.
"""
import os
import json
import requests
from datetime import datetime

_ALPACA_BASE = "https://paper-api.alpaca.markets"
_KEY    = __import__("jarvis_secrets").ALPACA_PAPER_KEY
_SECRET = __import__("jarvis_secrets").ALPACA_PAPER_SECRET


def _hdr():
    return {"APCA-API-KEY-ID": _KEY, "APCA-API-SECRET-KEY": _SECRET}


def _alpaca_positions():
    try:
        r = requests.get(f"{_ALPACA_BASE}/v2/positions", headers=_hdr(), timeout=8)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def _alpaca_equity():
    try:
        r = requests.get(f"{_ALPACA_BASE}/v2/account", headers=_hdr(), timeout=8)
        return float(r.json().get("equity", 0)) if r.status_code == 200 else 0.0
    except Exception:
        return 0.0


def _options_book():
    try:
        import paper_trades_store as pts
        data = pts.read()
        return [t for t in data.get("trades", []) if t.get("status") == "paper_open"]
    except Exception:
        return []


def _kalshi_bets():
    try:
        import jarvis_position_monitor as jpm
        return jpm.get_open_bets()
    except Exception:
        return []


def get_portfolio() -> dict:
    """Consolidated, read-only snapshot across all bots/domains."""
    import crypto_risk
    pos = _alpaca_positions()
    equity = _alpaca_equity()

    stock_positions, crypto_positions = [], []
    for p in pos:
        sym = p.get("symbol", "")
        mv = abs(float(p.get("market_value", 0) or 0))
        entry = {"symbol": sym, "market_value": round(mv, 2),
                 "qty": p.get("qty"),
                 "unrealized_pl": round(float(p.get("unrealized_pl", 0) or 0), 2)}
        (crypto_positions if crypto_risk.is_crypto_symbol(sym) else stock_positions).append(entry)

    opts = _options_book()
    options_cost = sum(float(t.get("cost_per_contract", 0) or 0) for t in opts)
    kbets = _kalshi_bets()

    stock_exp = sum(e["market_value"] for e in stock_positions)
    crypto_exp = sum(e["market_value"] for e in crypto_positions)

    by_symbol = {}
    for e in stock_positions + crypto_positions:
        by_symbol[e["symbol"]] = round(by_symbol.get(e["symbol"], 0) + e["market_value"], 2)

    gross = stock_exp + crypto_exp + options_cost
    return {
        "equity": round(equity, 2),
        "gross_exposure": round(gross, 2),
        "exposure_pct": round(gross / equity * 100, 1) if equity else None,
        "stock":   {"exposure": round(stock_exp, 2),  "positions": stock_positions},
        "crypto":  {"exposure": round(crypto_exp, 2), "positions": crypto_positions},
        "options": {"open_cost": round(options_cost, 2), "open_count": len(opts)},
        "kalshi":  {"open_bets": len(kbets)},
        "by_symbol": by_symbol,
        "n_positions": len(stock_positions) + len(crypto_positions),
    }


def symbol_exposure(symbol, portfolio=None) -> float:
    """Total Alpaca $ exposure to a base symbol (BTC matches BTCUSD, etc.)."""
    p = portfolio or get_portfolio()
    s = str(symbol).upper()
    return sum(v for k, v in p.get("by_symbol", {}).items() if s in k.upper())


def concentration_warnings(portfolio=None, symbol_pct=0.25, asset_class_pct=0.6) -> list:
    """Human-readable over-concentration warnings (no blocking)."""
    p = portfolio or get_portfolio()
    warns = []
    eq = p.get("equity") or 0
    if not eq:
        return warns
    for sym, mv in p.get("by_symbol", {}).items():
        if mv / eq > symbol_pct:
            warns.append(f"{sym} {mv/eq:.0%} of equity (${mv:,.0f})")
    if p["stock"]["exposure"] / eq > asset_class_pct:
        warns.append(f"stocks {p['stock']['exposure']/eq:.0%} of equity")
    if p["crypto"]["exposure"] / eq > asset_class_pct:
        warns.append(f"crypto {p['crypto']['exposure']/eq:.0%} of equity")
    return warns


def format_portfolio(portfolio=None) -> str:
    p = portfolio or get_portfolio()
    pct = f" ({p['exposure_pct']}%)" if p.get("exposure_pct") is not None else ""
    return ("PORTFOLIO (all bots)\n"
            f"Equity: ${p['equity']:,.0f} | Gross: ${p['gross_exposure']:,.0f}{pct}\n"
            f"Stocks: ${p['stock']['exposure']:,.0f} ({len(p['stock']['positions'])}) | "
            f"Crypto: ${p['crypto']['exposure']:,.0f} ({len(p['crypto']['positions'])})\n"
            f"Options book: ${p['options']['open_cost']:,.0f} ({p['options']['open_count']}) | "
            f"Kalshi bets: {p['kalshi']['open_bets']}")


# ── HARD GLOBAL CEILING (item #2 phase 2) ────────────────────────────────────
# Enforced limits that BLOCK new entries across all bots. Fail-OPEN on data
# error (a transient Alpaca outage must not freeze every bot), same philosophy
# as crypto_risk. Limits are deliberately generous vs current position sizing —
# the active protection today is the equity-drawdown breaker; the concentration
# caps are infrastructure that bites only if position sizes grow.
MAX_DRAWDOWN_PCT = 0.12   # halt ALL new entries if equity is ≥12% below its high-water mark
MAX_GROSS_PCT    = 0.85   # halt if gross exposure would exceed 85% of equity (leverage/deployment)
# Concentration caps are measured against DEPLOYED capital (the live stock+crypto
# position book), NOT total equity — so they bite regardless of idle equity size.
MAX_SYMBOL_PCT   = 0.40   # block a symbol exceeding 40% of deployed capital
MAX_SECTOR_PCT   = 0.60   # block a stock sector exceeding 60% of deployed capital
# Bootstrap floor: a lone position is 100% of a near-empty book, so concentration
# is measured against max(deployed, this) — lets the first ~3 entries open before
# the caps engage on a real book. ~3 typical $500 trade sizes.
MIN_DEPLOYED_BASIS = 1500.0
_HWM_FILE        = "/root/jarvis/portfolio_hwm.json"


def _update_hwm(equity):
    """Read+bump the equity high-water mark (best-effort, atomic). Returns HWM.
    Monotonic: a lost concurrent update only understates drawdown (fail-safe)."""
    try:
        data = {}
        if os.path.exists(_HWM_FILE):
            with open(_HWM_FILE) as f:
                data = json.load(f) or {}
        hwm = float(data.get("equity_hwm", 0) or 0)
        if equity > hwm:
            hwm = equity
            tmp = _HWM_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"equity_hwm": hwm, "updated": datetime.now().isoformat()}, f)
            os.replace(tmp, _HWM_FILE)
        return hwm
    except Exception:
        return 0.0


def _sector_of(symbol):
    """Map a ticker to its level5 sector ('tech'/'finance'/…), or None."""
    try:
        import jarvis_level5 as _l5
        s = str(symbol).upper().replace("USD", "")
        for sector, tickers in _l5.SECTORS.items():
            if s in tickers:
                return sector
    except Exception:
        pass
    return None


def drawdown_halted(portfolio=None):
    """(halted, reason) for the GLOBAL equity-drawdown breaker only. Used by
    master to broadcast a halt flag and by can_open()."""
    try:
        p = portfolio or get_portfolio()
        eq = p.get("equity") or 0
        if not eq:
            return False, ""
        hwm = _update_hwm(eq)
        dd = (hwm - eq) / hwm if hwm > 0 else 0.0
        if dd >= MAX_DRAWDOWN_PCT:
            return True, f"global drawdown {dd:.1%} ≥ {MAX_DRAWDOWN_PCT:.0%} (eq ${eq:,.0f} vs HWM ${hwm:,.0f})"
        return False, ""
    except Exception:
        return False, ""


def can_open(symbol=None, add_notional=0.0, portfolio=None):
    """HARD GATE — (ok: bool, reason: str). Call before every new BUY/entry on
    the shared Alpaca account. Blocks on: equity drawdown vs high-water mark,
    gross exposure ceiling (vs equity), and per-symbol / per-sector-stocks
    concentration (vs DEPLOYED capital). Fail-OPEN on any data error."""
    try:
        p = portfolio or get_portfolio()
        eq = p.get("equity") or 0
        if not eq:
            return True, ""  # no equity data → fail open
        add = float(add_notional or 0)

        halted, reason = drawdown_halted(p)
        if halted:
            return False, reason

        gross = p.get("gross_exposure") or 0
        if (gross + add) / eq > MAX_GROSS_PCT:
            return False, f"gross exposure {(gross+add)/eq:.0%} > {MAX_GROSS_PCT:.0%} cap (${gross+add:,.0f}/${eq:,.0f})"

        # Concentration vs DEPLOYED capital (live position book + this new add),
        # not total equity — so the caps constrain regardless of idle equity.
        deployed = (p.get("stock") or {}).get("exposure", 0) + (p.get("crypto") or {}).get("exposure", 0) + add
        basis = max(deployed, MIN_DEPLOYED_BASIS)  # bootstrap floor (see constant)
        if symbol and basis > 0:
            cur = symbol_exposure(symbol, p)
            if (cur + add) / basis > MAX_SYMBOL_PCT:
                return False, f"{symbol} would be {(cur+add)/basis:.0%} of deployed capital > {MAX_SYMBOL_PCT:.0%} cap"
            sector = _sector_of(symbol)
            if sector:
                sec_exp = sum(mv for sym, mv in p.get("by_symbol", {}).items()
                              if _sector_of(sym) == sector)
                if (sec_exp + add) / basis > MAX_SECTOR_PCT:
                    return False, f"sector {sector} would be {(sec_exp+add)/basis:.0%} of deployed capital > {MAX_SECTOR_PCT:.0%} cap"
        return True, ""
    except Exception:
        return True, ""  # fail open


if __name__ == "__main__":
    print(format_portfolio())
    _ok, _why = can_open("BTC", 500)
    print(f"can_open(BTC, $500): {_ok} {_why}")
    print("drawdown_halted:", drawdown_halted())

