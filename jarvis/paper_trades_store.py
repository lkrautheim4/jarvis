"""Shared, lock-protected access to paper_trades.json.

Four bots read-modify-write this file concurrently — options_grader (every
5 min), jarvis_options_brain (daemon), jarvis_options_scanner (prime hours)
and grade_paper_trades. Each previously did its own whole-file json.load +
json.dump with NO coordination, so overlapping writes clobbered one another
(a close reverted to open, a freshly-appended trade vanished, or the JSON
truncated mid-write). This module funnels every access through:

  * an advisory flock on a sidecar lock file, held across the whole
    read-modify-write so the operation is atomic w.r.t. other processes;
  * an atomic write (temp file + os.replace) so a crash mid-write can never
    leave a half-written / truncated paper_trades.json.

All writers MUST go through update()/read() here for the locking to be
effective. Compute slow work (network, yfinance) BEFORE calling update() and
keep the mutator fast — the lock is held for the mutator's duration.
"""
import os, json, fcntl, tempfile
from contextlib import contextmanager

PAPER_TRADES_FILE = "/root/jarvis/paper_trades.json"
_LOCK_FILE = PAPER_TRADES_FILE + ".lock"

# Per-underlying risk caps (issue #1 — runaway single-name concentration).
# Checked inside the lock by would_exceed_cap() before a writer appends, so two
# concurrent scans can't both slip past the cap. Tune to taste.
MAX_OPEN_PER_TICKER = 3       # max simultaneously-open positions in one underlying
MAX_TICKER_COST     = 4000.0  # max total cost_per_contract ($) open in one underlying

# Training-mode sizing brake (until the system has proven itself). While in
# training mode we hard-cap per-contract cost and TOTAL open book exposure.
# "Graduated" = at least GRAD_MIN_TRADES graded (closed w/ result) trades AND a
# win rate >= GRAD_MIN_WINRATE, at which point these two extra limits relax
# (the per-ticker caps above still always apply).
MAX_CONTRACT_COST = 500.0     # no single contract may cost more than this
MAX_BOOK_COST     = 5000.0    # total open-book cost ceiling
GRAD_MIN_TRADES   = 50
GRAD_MIN_WINRATE  = 0.60


def total_open_cost(data):
    return sum(float(t.get("cost_per_contract", 0) or 0)
               for t in data.get("trades", []) if t.get("status") == "paper_open")


def graded_record(data):
    """(graded_count, win_rate) over closed trades with a result."""
    closed = [t for t in data.get("trades", [])
              if t.get("status") == "paper_closed" and t.get("result")]
    if not closed:
        return 0, 0.0
    wins = sum(1 for t in closed if t.get("result") == "WIN")
    return len(closed), wins / len(closed)


def in_training_mode(data):
    """True (sizing brake active) until 50+ graded trades at >=60% win rate."""
    n, wr = graded_record(data)
    return not (n >= GRAD_MIN_TRADES and wr >= GRAD_MIN_WINRATE)

# Junk-contract guard (issue #4 — deep-OTM, near-worthless contracts like a $250
# AMD put on a ~$530 stock @ $0.14 were being logged, polluting win-rate stats
# and wasting signal). Reject illiquid / absurdly-far-OTM contracts at log time.
MIN_PREMIUM       = 0.15   # $ per share — below this the contract is junk/illiquid
MAX_MONEYNESS_PCT = 0.25   # strike must be within 25% of spot price


def is_junk_contract(spot, strike, premium):
    """(is_junk, reason) — True if premium too small or strike too far from spot.

    FAILS CLOSED: if the inputs can't be evaluated (missing/garbage spot, strike
    or premium) the contract is treated as junk. The previous version returned
    "not junk" on any exception, so a single bad field would wave a contract
    straight through the filter — exactly the kind of hole that let malformed
    setups (AMD $250 put, AAPL $145 put) get logged."""
    try:
        if premium is None or float(premium) < MIN_PREMIUM:
            return True, f"premium ${float(premium or 0):.2f} < ${MIN_PREMIUM:.2f} floor"
        spot = float(spot or 0)
        if spot <= 0:
            return True, f"no usable spot price ({spot})"
        dist = abs(float(strike) - spot) / spot
        if dist > MAX_MONEYNESS_PCT:
            return True, f"strike ${strike} is {dist*100:.0f}% from spot ${spot:.0f} (>{MAX_MONEYNESS_PCT*100:.0f}%)"
    except Exception as e:
        return True, f"unparseable contract (spot={spot}, strike={strike}, premium={premium}): {e}"
    return False, ""


@contextmanager
def _lock():
    f = open(_LOCK_FILE, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def _read_unlocked():
    try:
        with open(PAPER_TRADES_FILE) as f:
            return json.load(f)
    except Exception:
        return {"trades": []}


def _write_atomic(data):
    d = os.path.dirname(PAPER_TRADES_FILE) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".pt_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, PAPER_TRADES_FILE)   # atomic on POSIX
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update(mutator):
    """Atomically read -> mutator(data) -> write, holding the lock throughout.

    `mutator(data)` mutates the loaded dict in place; whatever it returns is
    returned by update(). Keep it fast — no network inside.
    """
    with _lock():
        data = _read_unlocked()
        if "trades" not in data:
            data["trades"] = []
        data.pop("date", None)   # strip stale orphan top-level key (no writer/reader)
        result = mutator(data)
        _write_atomic(data)
        return result


def read():
    """Locked, consistent snapshot read (no write)."""
    with _lock():
        return _read_unlocked()


def open_exposure(data, ticker):
    """(open_count, total_open_cost) for `ticker` given an already-loaded dict."""
    opens = [t for t in data.get("trades", [])
             if t.get("ticker") == ticker and t.get("status") == "paper_open"]
    return len(opens), sum(float(t.get("cost_per_contract", 0) or 0) for t in opens)


def would_exceed_cap(data, ticker, new_cost,
                     max_count=MAX_OPEN_PER_TICKER, max_cost=MAX_TICKER_COST,
                     trade=None):
    """True if adding a position would breach a limit OR is a junk contract.

    Enforces (a) the junk-contract guard, (b) the training-mode sizing brake —
    per-contract + total-book cost — and (c) the always-on per-ticker count/cost
    caps. Call inside an update() mutator so the check + append are atomic.

    Pass the candidate `trade` dict so the junk guard is enforced HERE, at the
    single write chokepoint, instead of relying on each caller to remember to
    call is_junk_contract() first. Callers that forget (or a stale daemon, or a
    future writer) can no longer slip a deep-OTM / near-worthless contract past
    the filter."""
    nc = float(new_cost or 0)
    # (a) junk-contract guard — central, can't be bypassed
    if trade is not None:
        junk, reason = is_junk_contract(
            trade.get("entry_price"), trade.get("strike"), trade.get("premium"))
        if junk:
            return True, f"junk contract: {reason}"
    # (b) training-mode sizing brake
    if in_training_mode(data):
        if nc > MAX_CONTRACT_COST:
            return True, f"contract ${nc:.0f} > ${MAX_CONTRACT_COST:.0f}/contract cap (training mode)"
        book = total_open_cost(data)
        if book + nc > MAX_BOOK_COST:
            return True, f"book ${book:.0f}+${nc:.0f} > ${MAX_BOOK_COST:.0f} book cap (training mode)"
    # (c) per-ticker caps (always)
    count, cost = open_exposure(data, ticker)
    if count + 1 > max_count:
        return True, f"{ticker} at position cap ({count}/{max_count} open)"
    if cost + nc > max_cost:
        return True, f"{ticker} at cost cap (${cost:.0f}+${nc:.0f} > ${max_cost:.0f})"
    return False, ""
