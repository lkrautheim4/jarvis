"""Shared crypto-exposure guard for the shared Alpaca paper account.

Several bots buy crypto on ONE Alpaca account — jarvis_alpha, jarvis_futures,
jarvis_master (scalp) and jarvis_alpha_v2. A per-bot cap can't bound the TOTAL,
because each bot only sees its own intent. This module queries the live account
(so it reflects ALL bots' positions together) and is the single source of truth
for the ceiling: every crypto buy must pass can_add_crypto() first.

Fail-open on API error (returns allowed) — a transient Alpaca outage shouldn't
freeze trading; the cap re-applies as soon as the API recovers.
"""
import requests

CRYPTO_EXPOSURE_CAP = 3000.0   # max total crypto market value ($) across all bots

_ALPACA_BASE = "https://paper-api.alpaca.markets"
_KEY    = __import__("jarvis_secrets").ALPACA_PAPER_KEY
_SECRET = __import__("jarvis_secrets").ALPACA_PAPER_SECRET
_CRYPTO_TOKENS = ("BTC", "XBT", "ETH", "SOL", "AVAX", "DOGE", "LTC", "XRP", "USDC", "USDT", "LINK", "UNI", "AAVE")


def _positions():
    try:
        r = requests.get(f"{_ALPACA_BASE}/v2/positions",
                         headers={"APCA-API-KEY-ID": _KEY, "APCA-API-SECRET-KEY": _SECRET},
                         timeout=8)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def is_crypto_symbol(sym):
    s = str(sym).replace("/", "").upper()
    return "USD" in s and any(tok in s for tok in _CRYPTO_TOKENS)


def crypto_exposure(positions=None):
    """Total $ market value of all crypto positions on the account."""
    if positions is None:
        positions = _positions()
    return sum(abs(float(p.get("market_value", 0) or 0))
               for p in positions if is_crypto_symbol(p.get("symbol", "")))


def can_add_crypto(size, cap=CRYPTO_EXPOSURE_CAP):
    """(ok, exposure, reason) — False if current crypto exposure + `size` would
    breach `cap`. Call before every crypto BUY (not sells, which reduce risk)."""
    cx = crypto_exposure()
    if cx + float(size or 0) > cap:
        return False, cx, f"crypto exposure ${cx:,.0f}+${float(size or 0):,.0f} > ${cap:,.0f} cap"
    return True, cx, ""
