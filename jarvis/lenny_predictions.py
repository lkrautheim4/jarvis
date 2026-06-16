"""
lenny_predictions.py — Jarvis BTC Prediction Bot
Full memory + learning loop. Jarvis never forgets.
"""

import requests, json, time, logging, os, sys, math, sqlite3
from datetime import datetime, timezone
import jarvis_brain
import btc_memory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("lenny_predictions")

# ── Config ──────────────────────────────────────────────────────────────────
from jarvis_secrets import CLAUDE_API_KEY, TG_TOKEN_LENNY
TG_TOKEN        = TG_TOKEN_LENNY   # @Lenny_predictions_bot — stored in secrets.json
TG_CHAT_ID      = "7534553840"
KALSHI_API_KEY  = "f3c367c6-92fe-455f-ae54-2dcef68d07a7"
DB_PATH         = "/root/jarvis/jarvis_memory.db"

SYMBOL          = "BTC"
CHECK_INTERVAL  = 3600          # 1 hour
WATCH_FILE      = "/root/jarvis/lenny_watch.json"   # this bot's own WATCH store

# ── Edge-gate thresholds (tune here, nowhere else) ────────────────────────────
EDGE_THRESHOLD    = 0.08   # min model-vs-market edge to place any bet
NEAR_CERTAIN      = 0.90   # market priced this confident (either side) triggers stricter gate
NEAR_CERTAIN_EDGE = 0.15   # edge required when market is near-certain (kills penny-edge bets)
WATCH_TTL       = 7200          # a watch anchors for 2h, then expires

def make_targets(price: float, watch_price: float | None = None) -> list:
    """6 hourly targets, $100-spaced.
    - With a watch price set (user 'WATCH <price>'): anchor the ladder on THAT level
      (nearest $100), so the call is about the price the user is actually watching.
    - Otherwise: fall back to the LIVE price rounded UP to the next $100, then +0..+500."""
    if watch_price:
        base = int(round(watch_price / 100.0) * 100)   # nearest $100 to the watch level (anchor)
    else:
        base = int(math.ceil(price / 100.0) * 100)     # live price ceiling (no watch)
    return [base + 100 * i for i in range(6)]


def make_range(price: float) -> tuple:
    """Dynamic prediction band centered on the live price (±0.2%)."""
    return (price * 0.998, price * 1.002)


def save_watched_strike(strike: float):
    """Persist this bot's own WATCH strike (set via the WATCH command)."""
    with open(WATCH_FILE, "w") as f:
        json.dump({"strike": float(strike), "ts": time.time()}, f)


def load_watched_strike() -> float | None:
    """This bot's own WATCH strike if set and still fresh (< WATCH_TTL), else None."""
    try:
        d = json.load(open(WATCH_FILE))
        if time.time() - d.get("ts", 0) > WATCH_TTL:
            return None
        return float(d["strike"])
    except Exception:
        return None


def clear_watched_strike():
    try:
        os.remove(WATCH_FILE)
    except OSError:
        pass


def get_watch_strike() -> float | None:
    """Active WATCH strike to anchor targets, or None. Prefers this bot's own WATCH
    (lenny_watch.json, set via the WATCH command), then falls back to the shared
    session (active_session.json from jarvis_master's WATCH; 2h expiry). Fully guarded
    so a missing/stale watch never breaks a cycle."""
    own = load_watched_strike()
    if own is not None:
        return own
    try:
        import jarvis_session
        s = jarvis_session.get_session()
        return float(s["strike"]) if s and s.get("strike") else None
    except Exception:
        return None

# ── Telegram ─────────────────────────────────────────────────────────────────
def tg(msg: str):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10
        )
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")


# ── Price / RSI / Momentum ────────────────────────────────────────────────────
def _kraken_price(symbol: str) -> float | None:
    # Keyless fallback when CoinGecko rate-limits (free tier 429s frequently).
    pair = {"BTC": "XBTUSD", "ETH": "ETHUSD"}.get(symbol)
    if not pair:
        return None
    try:
        r = requests.get(f"https://api.kraken.com/0/public/Ticker?pair={pair}", timeout=10)
        d = r.json()
        if d.get("error"):
            return None
        res = d.get("result") or {}
        k = next(iter(res), None)
        return float(res[k]["c"][0]) if k else None
    except Exception as e:
        log.error(f"Kraken price error ({symbol}): {e}")
        return None


def get_price(symbol: str) -> float | None:
    coin = {"BTC": "bitcoin", "ETH": "ethereum"}.get(symbol)
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin, "vs_currencies": "usd"},
            timeout=10
        )
        # CoinGecko returns an error/status JSON (no coin key) when rate-limited.
        data = r.json()
        if isinstance(data, dict) and coin in data and "usd" in data[coin]:
            return data[coin]["usd"]
        log.warning(f"CoinGecko price unavailable for {symbol} (HTTP {r.status_code}); trying Kraken")
    except Exception as e:
        log.warning(f"CoinGecko price error ({symbol}): {e}; trying Kraken")
    price = _kraken_price(symbol)
    if price is None:
        log.error(f"Price error: no source returned a price for {symbol}")
    return price


def get_rsi(symbol: str, period: int = 14) -> float:
    try:
        coin = {"BTC": "bitcoin", "ETH": "ethereum"}.get(symbol)
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": "2", "interval": "hourly"},
            timeout=15
        )
        prices = [p[1] for p in r.json()["prices"]][-period-1:]
        return _rsi_from_prices(prices, period)
    except Exception as e:
        log.warning(f"CoinGecko RSI unavailable ({symbol}): {e}; trying Kraken")
    prices = _kraken_ohlc_closes(symbol, period + 1)
    if prices:
        return _rsi_from_prices(prices, period)
    log.warning(f"RSI: no source for {symbol}, defaulting to 50.0")
    return 50.0


def _rsi_from_prices(prices: list, period: int = 14) -> float:
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i-1]
        (gains if d > 0 else losses).append(abs(d))
    if not gains:
        return 50.0
    ag = sum(gains) / len(gains)
    al = sum(losses) / len(losses) if losses else 0.001
    rs = ag / al
    return round(100 - (100 / (1 + rs)), 1)


def _kraken_ohlc_closes(symbol: str, count: int) -> list:
    pair = {"BTC": "XBTUSD", "ETH": "ETHUSD"}.get(symbol)
    if not pair:
        return []
    try:
        r = requests.get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=60", timeout=10)
        d = r.json()
        if d.get("error"):
            return []
        res = d.get("result") or {}
        k = next((kk for kk in res if kk != "last"), None)
        return [float(c[4]) for c in res[k]][-count:] if k else []
    except Exception as e:
        log.warning(f"Kraken OHLC error ({symbol}): {e}")
        return []


def get_momentum(symbol: str) -> dict:
    try:
        coin = {"BTC": "bitcoin", "ETH": "ethereum"}.get(symbol)
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin}",
            params={"localization": "false", "tickers": "false",
                    "community_data": "false", "developer_data": "false"},
            timeout=10
        )
        data = r.json()["market_data"]
        return {
            "1h":  round(data["price_change_percentage_1h_in_currency"]["usd"], 2),
            "4h":  0.0,
            "24h": round(data["price_change_percentage_24h"], 2),
            "7d":  round(data["price_change_percentage_7d"], 2),
        }
    except Exception as e:
        # Non-fatal: callers treat zeros as "neutral momentum". Warn (not error)
        # so transient CoinGecko 429s don't trip the watchdog's error counter.
        log.warning(f"Momentum unavailable ({symbol}): {e}; using neutral")
        return {"1h": 0.0, "4h": 0.0, "24h": 0.0, "7d": 0.0}


# ── Kalshi ────────────────────────────────────────────────────────────────────
def get_kalshi_odds(symbol: str) -> list:
    """Fetch open KXBTCD markets. Returns list of dicts with ticker, strike,
    yes_price/no_price (in dollars 0-1), and display yes_bid/no_bid (cents)."""
    try:
        r = requests.get(
            "https://api.elections.kalshi.com/trade-api/v2/markets",
            params={"status": "open", "series_ticker": "KXBTCD", "limit": 20},
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            timeout=10
        )
        markets = r.json().get("markets", [])
        out = []
        for m in markets:
            yes_d = m.get("yes_bid_dollars") or 0.0
            no_d  = m.get("no_bid_dollars")  or 0.0
            out.append({
                "ticker":     m.get("ticker", ""),
                "title":      m.get("title", ""),
                "strike":     float(m.get("floor_strike") or 0),
                "close_time": m.get("close_time", ""),
                "yes_price":  round(float(yes_d), 4),
                "no_price":   round(float(no_d), 4),
                # Legacy cent-denominated fields kept for prompt display
                "yes_bid":    int(round(float(yes_d) * 100)),
                "no_bid":     int(round(float(no_d) * 100)),
            })
        return out
    except Exception as e:
        log.error(f"Kalshi error: {e}")
        return []


def select_kalshi_market(price: float, markets: list) -> dict | None:
    """Pick the tradeable Kalshi market whose strike is closest to the current
    price, preferring the lowest one above price (cheapest YES bet to evaluate)."""
    if not markets:
        return None
    above = [m for m in markets if m["strike"] >= price]
    below = [m for m in markets if m["strike"] < price]
    if above:
        return min(above, key=lambda m: m["strike"])
    if below:
        return max(below, key=lambda m: m["strike"])
    return None


def _log_kalshi_pred(symbol: str, ts: str, target: float, bet: str,
                     prob: str, yes_price: float, no_price: float,
                     reason: str, market_ticker: str) -> int | None:
    """Write one prediction row to kalshi_bets with real market data.
    Returns the new row id, or None on error. Skips SKIP bets (no contract placed)."""
    if bet == "SKIP":
        return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        # Dedup: don't double-log if called twice within 60 seconds for same symbol+bet
        cutoff = ts[:16]   # minute-level prefix
        existing = conn.execute(
            "SELECT id FROM kalshi_bets WHERE symbol=? AND market=? AND ts >= ?",
            (symbol, market_ticker, cutoff)
        ).fetchone()
        if existing:
            conn.close()
            return existing[0]
        cur = conn.execute("""
            INSERT INTO kalshi_bets(ts, symbol, strike, bet, prob, yes_price, no_price,
                                    reason, market, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'auto')
        """, (ts, symbol, target, bet, prob, yes_price, no_price, reason, market_ticker))
        row_id = cur.lastrowid
        conn.commit()
        conn.close()
        return row_id
    except Exception as e:
        log.error(f"_log_kalshi_pred DB error: {e}")
        return None


# ── Edge gate ─────────────────────────────────────────────────────────────────
def _parse_pct(s) -> float | None:
    """Numeric percent (0-100) from strings like '71%', '71', '71.5%'. None if not parseable (e.g. '?')."""
    try:
        return float(str(s).strip().rstrip("%").strip())
    except (ValueError, TypeError):
        return None


def _edge_gate(target_prob_str: str, yes_price: float) -> tuple[str, float]:
    """Bet only when the model DISAGREES with the Kalshi market price by a profitable margin.
    edge = model_prob - kalshi_yes_price
      edge >  threshold → YES  (model thinks YES is underpriced)
      edge < -threshold → NO   (model thinks YES is overpriced)
      else              → SKIP (no edge, no bet)
    Near-certain markets (yes_price ≥ 0.90 or ≤ 0.10) require a higher edge
    to prevent penny-edge bets (e.g. NO at $0.99 for a $0.01 payout)."""
    raw = _parse_pct(target_prob_str)
    if raw is None:
        return "SKIP", 0.0
    model_prob = raw / 100.0
    edge = round(model_prob - yes_price, 4)
    near_certainty = yes_price >= NEAR_CERTAIN or yes_price <= (1.0 - NEAR_CERTAIN)
    threshold = NEAR_CERTAIN_EDGE if near_certainty else EDGE_THRESHOLD
    if edge > threshold:
        return "YES", edge
    elif edge < -threshold:
        return "NO", edge
    else:
        return "SKIP", edge


def _pct_str(v) -> str:
    """Render a numeric confidence (0-100) as 'NN%', or '?' if not numeric."""
    try:
        return f"{int(round(float(v)))}%"
    except (ValueError, TypeError):
        return "?"


# Forced structured output: with tool_choice pinned to this tool, Claude MUST return
# these fields (enum-constrained bet, numeric probs) instead of prose that falls
# through to the SKIP fallback. strict=True guarantees the schema is honored.
PRED_TOOL = {
    "name": "submit_prediction",
    "description": "Submit your BTC prediction for the hourly Kalshi target.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "target_prob":     {"type": "integer", "description": "Confidence 0-100 that price ends ABOVE the target by the deadline."},
            "predicted_price": {"type": "number",  "description": "Single best-guess price (USD) at the deadline."},
            "range_prob":      {"type": "integer", "description": "Confidence 0-100 that price stays within the given prediction range."},
            "bet":             {"type": "string", "enum": ["YES", "NO", "SKIP"], "description": "YES = price hits target; NO = misses; SKIP if uncertain."},
            "reason":          {"type": "string", "description": "One brutally specific sentence. No hedging."},
        },
        "required": ["target_prob", "predicted_price", "range_prob", "bet", "reason"],
        "additionalProperties": False,
    },
}


# ── Claude ────────────────────────────────────────────────────────────────────
def ask_claude(symbol: str, price: float, target: float,
               low: float, high: float,
               rsi: float, momentum: dict,
               kalshi_markets: list | None = None,
               selected_market: dict | None = None) -> dict:
    try:
        shared    = jarvis_brain.read_brain()
        mood      = shared.get("market_mood", "neutral")
        btc_sig   = shared.get("btc_signal", "neutral")
        # Use pre-fetched markets passed in from run_prediction (avoids duplicate API call)
        kalshi = kalshi_markets or []
        kalshi_txt = "; ".join(
            [f"${m['strike']:,.2f} YES={m['yes_bid']}c NO={m['no_bid']}c" for m in kalshi[:5]]
        ) or "No Kalshi data"
        sr        = btc_memory.get_support_resistance()
        sr_txt    = (f"Support: ${sr.get('support',0):,.0f} | "
                     f"Resistance: ${sr.get('resistance',0):,.0f} | "
                     f"7d Avg: ${sr.get('avg',0):,.0f}") if sr else "Not enough data yet"
        memory_ctx = btc_memory.build_context()

        # Determine deadline from the selected market's close time if available
        if selected_market and selected_market.get("close_time"):
            deadline = selected_market["close_time"][:16].replace("T", " ") + " UTC"
        else:
            deadline = str((datetime.now(timezone.utc).hour + 1) % 24) + ":00 UTC"

        # Show selected market entry price context if available
        market_line = ""
        if selected_market:
            market_line = (f"\nSelected market: {selected_market['ticker']}"
                           f"  YES=${selected_market['yes_price']:.2f}"
                           f"  NO=${selected_market['no_price']:.2f}")

        prompt = f"""You are Jarvis, a sharp crypto trading AI with a perfect memory. You track every price, every prediction, every outcome. You are brutal, precise, and never vague.

=== CURRENT MARKET ===
{symbol} @ ${price:,.2f}
Kalshi target: above ${target:,.2f} by {deadline}{market_line}
Prediction range: ${low:,.0f} - ${high:,.0f}
RSI: {rsi} | 1h: {momentum.get('1h',0):+.2f}% | 24h: {momentum.get('24h',0):+.2f}% | 7d: {momentum.get('7d',0):+.2f}%
Market mood: {mood} | BTC signal: {btc_sig}

=== SUPPORT / RESISTANCE ===
{sr_txt}

=== KALSHI MARKETS (open contracts) ===
{kalshi_txt}

=== YOUR MEMORY ===
{memory_ctx}

=== YOUR TASK ===
Based on ALL of the above — your track record, price history, momentum, S/R levels, and Kalshi pricing — make your sharpest call.

Rules (these are the exact thresholds the system enforces on your bet):
- bet YES only if you are >=65% confident price will be ABOVE the target by the deadline
- bet NO only if you are <=20% confident it hits (i.e. >=80% confident it MISSES)
- otherwise bet SKIP
- target_prob is your confidence (0-100) that price ends ABOVE the target
- if the Kalshi YES price is mispriced vs your probability, note that edge in your reason
- be brutally specific in one sentence; no hedging

Submit your call by calling the submit_prediction tool. Do not write any prose outside the tool call."""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":          CLAUDE_API_KEY,
                "anthropic-version":  "2023-06-01",
                "content-type":       "application/json"
            },
            json={
                "model":       "claude-sonnet-4-6",
                "max_tokens":  500,
                "tools":       [PRED_TOOL],
                "tool_choice": {"type": "tool", "name": "submit_prediction"},  # force structured output
                "messages":    [{"role": "user", "content": prompt}]
            },
            timeout=20
        )
        resp = r.json()
        if not isinstance(resp, dict):
            log.error(f"Claude unexpected response type: {type(resp).__name__}")
            return _fallback()
        if "error" in resp:
            log.error(f"Claude API error: {resp['error']}")
            return _fallback()

        # Forced tool_choice → answer is the tool_use block's validated input dict.
        # Guard every level: content may be missing/non-list, blocks may be non-dicts,
        # input may be absent or not a dict on an unexpected response shape.
        content = resp.get("content")
        data = None
        if isinstance(content, list):
            for b in content:
                if (isinstance(b, dict) and b.get("type") == "tool_use"
                        and b.get("name") == "submit_prediction"):
                    inp = b.get("input")
                    if isinstance(inp, dict):
                        data = inp
                        break
        if not data:
            log.error(f"Claude no usable tool_use (stop={resp.get('stop_reason')}): {content}")
            return _fallback()

        target_prob_pct = _pct_str(data.get("target_prob"))
        range_prob_pct  = _pct_str(data.get("range_prob"))
        pp              = data.get("predicted_price")
        predicted_str   = f"{float(pp):,.0f}" if isinstance(pp, (int, float)) else "?"

        # Edge gate: compare model probability to Kalshi market price
        kalshi_yes = selected_market["yes_price"] if selected_market else 0.5
        bet, edge  = _edge_gate(target_prob_pct, kalshi_yes)
        edge_tag   = f"edge={edge:+.2f} model={target_prob_pct} kalshi={kalshi_yes:.2f}"
        log.info(f"Edge gate: {edge_tag} → {bet}")

        reason_base = str(data.get("reason", "No reason given")).strip()
        reason = f"{reason_base} [{edge_tag}]"

        return {
            "target_prob":     target_prob_pct,
            "predicted_price": predicted_str,
            "range_prob":      range_prob_pct,
            "bet":             bet,
            "reason":          reason,
        }

    except Exception as e:
        log.error(f"Claude error: {e}")
        return _fallback()


def _fallback() -> dict:
    return {
        "target_prob":     "?",
        "predicted_price": "?",
        "range_prob":      "?",
        "bet":             "SKIP",
        "reason":          "Claude unavailable",
    }


# ── Main prediction cycle ─────────────────────────────────────────────────────
def run_prediction(symbol: str):
    price = get_price(symbol)
    if not price:
        tg(f"⚠️ Could not fetch {symbol} price")
        return

    rsi      = get_rsi(symbol)
    momentum = get_momentum(symbol)

    # 1. Log this price tick to memory
    btc_memory.log_price(price, rsi, momentum)

    # 2. Grade last prediction now that we have a new price
    graded = btc_memory.grade_last_prediction(price)
    if graded:
        log.info("Graded last prediction")
    # Regime-aware grading: tags each prediction TRENDING/CHOP, marks bet-eligibility
    try:
        import btc_regime_grader
        rg = btc_regime_grader.grade(verbose=False)
        if rg:
            log.info(f"Regime grader: tagged {rg} prediction(s)")
    except Exception as e:
        log.error(f"Regime grader failed (non-fatal): {e}")

    # 3. Fetch real Kalshi markets once (used for both target selection and the Claude prompt)
    kalshi_markets = get_kalshi_odds(symbol)
    selected_market = select_kalshi_market(price, kalshi_markets)

    if selected_market:
        target         = selected_market["strike"]
        market_ticker  = selected_market["ticker"]
        yes_price      = selected_market["yes_price"]
        no_price       = selected_market["no_price"]
        log.info(f"Real Kalshi market selected: {market_ticker} strike=${target:,.2f}"
                 f" YES=${yes_price:.4f} NO=${no_price:.4f}")
    else:
        # Fallback to synthetic round-number target when no Kalshi markets available
        watch   = get_watch_strike()
        targets = make_targets(price, watch)
        if watch:
            target = int(round(watch / 100.0) * 100)
            log.info(f"Anchoring target on WATCH price ${watch:,.2f} → ${target:,}")
        else:
            above  = [t for t in targets if t > price]
            target = min(above) if above else max(targets)
        market_ticker  = ""
        yes_price      = 0.5
        no_price       = 0.5
        log.warning("No Kalshi markets available — falling back to synthetic target")

    low, high = make_range(price)

    # 4. Ask Claude (with full memory context and pre-fetched Kalshi markets)
    pred = ask_claude(symbol, price, target, low, high, rsi, momentum,
                      kalshi_markets=kalshi_markets, selected_market=selected_market)

    # 5. Log this prediction — skip write on API failure (never pollute DB with stubs)
    if pred.get("reason") == "Claude unavailable":
        log.error(f"run_prediction: Claude unavailable for {symbol} — skipping DB write")
    else:
        btc_memory.log_prediction(
            symbol, price, target, low, high,
            pred["target_prob"], pred["predicted_price"],
            pred["range_prob"],  pred["bet"], pred["reason"]
        )
        # Write a real-market bet row to kalshi_bets (source='auto') only when we have
        # an actual Kalshi ticker — never write synthetic placeholders to this table.
        if market_ticker:
            ts_now = datetime.now(timezone.utc).isoformat()
            row_id = _log_kalshi_pred(
                symbol=symbol, ts=ts_now, target=target, bet=pred["bet"],
                prob=pred["target_prob"], yes_price=yes_price, no_price=no_price,
                reason=pred["reason"], market_ticker=market_ticker
            )
            if row_id:
                log.info(f"Logged kalshi_bets row id={row_id} market={market_ticker} bet={pred['bet']}")

    # 6. Build Telegram message
    if selected_market and selected_market.get("close_time"):
        deadline = selected_market["close_time"][:16].replace("T", " ") + " UTC"
        market_tag = f"\nMarket: {selected_market['ticker']}"
    else:
        deadline  = str((datetime.now(timezone.utc).hour + 1) % 24) + ":00 UTC"
        market_tag = ""
    diff       = target - price
    diff_str   = f"UP ${diff:,.2f}" if diff > 0 else f"DOWN ${abs(diff):,.2f}"
    bet        = pred["bet"]
    bet_line   = {"YES": "🟢 BET YES", "NO": "🔴 BET NO", "SKIP": "⚪ SKIP"}.get(bet, "⚪ SKIP")
    stats_line = btc_memory.get_stats_line()
    sr         = btc_memory.get_support_resistance()
    sr_line    = (f"S: ${sr.get('support',0):,.0f} | R: ${sr.get('resistance',0):,.0f}"
                  if sr else "Building S/R levels...")

    msg = f"""🤖 JARVIS PREDICTIONS
{'='*24}
{symbol} @ ${price:,.2f}
Target: ${target:,.2f} ({diff_str}){market_tag}
Range:  ${low:,.0f} - ${high:,.0f}
Deadline: {deadline}
{'='*24}
{bet_line}
Target prob:  {pred['target_prob']}
Best guess:   ${pred['predicted_price']}
Range prob:   {pred['range_prob']}
{'='*24}
📊 RSI:{rsi} | 1h:{momentum.get('1h',0):+.1f}% | 24h:{momentum.get('24h',0):+.1f}%
📍 {sr_line}
{'='*24}
💡 {pred['reason']}
{'='*24}
📈 {stats_line}"""

    tg(msg)
    log.info(f"Prediction sent {symbol} target={target} bet={bet} prob={pred['target_prob']}")


# ── Commands (WATCH) ──────────────────────────────────────────────────────────
def handle_command(text: str):
    """Dispatch a Telegram command. Supports:
         WATCH <price>          → anchor predictions on that level + fire an immediate call
         WATCH OFF|STOP|CLEAR   → clear the watch (back to live-price targets)"""
    parts = text.strip().split()
    if not parts or parts[0].upper() != "WATCH":
        return
    if len(parts) >= 2 and parts[1].upper() in ("OFF", "STOP", "CLEAR"):
        clear_watched_strike()
        tg("👁 Watch cleared — predictions back to live-price targets.")
        return
    if len(parts) < 2:
        tg("Format: WATCH 61805   (or WATCH OFF)")
        return
    try:
        strike = float(parts[1].replace(",", "").replace("$", ""))
    except ValueError:
        tg(f"Couldn't read a price from '{parts[1]}'. Format: WATCH 61805")
        return
    save_watched_strike(strike)
    live = get_price(SYMBOL)
    if live:
        diff = live - strike
        pos  = "ABOVE" if diff >= 0 else "BELOW"
        tg(f"👁 WATCHING ${strike:,.0f}\n{SYMBOL}: ${live:,.0f} ({pos} by ${abs(diff):,.0f})\nRunning a prediction anchored on this level…")
    else:
        tg(f"👁 WATCHING ${strike:,.0f}\nRunning a prediction anchored on this level…")
    # Fire an immediate call anchored on the watched level (don't wait for the hourly tick).
    try:
        run_prediction(SYMBOL)
    except Exception as e:
        log.error(f"WATCH prediction error: {e}")


def poll_commands(offset):
    """Long-poll Telegram for WATCH commands. Returns the next update offset."""
    try:
        params = {"timeout": 10}
        if offset is not None:
            params["offset"] = offset
        r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                         params=params, timeout=20)
        updates = r.json().get("result", [])
    except Exception as e:
        log.warning(f"getUpdates error: {e}")
        return offset
    for u in updates:
        offset = u["update_id"] + 1
        msg = u.get("message", {})
        if str(msg.get("chat", {}).get("id", "")) != TG_CHAT_ID:
            continue
        text = (msg.get("text") or "").strip()
        if text:
            log.info(f"CMD: {text}")
            try:
                handle_command(text)
            except Exception as e:
                log.error(f"handle_command error: {e}")
    return offset


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("JARVIS PREDICTIONS BOT ONLINE — Memory active")
    tg("🧠 Jarvis Predictions online. Memory active. Send WATCH <price> to anchor a call.")

    # Drain any backlog so we only react to commands sent AFTER startup.
    tg_offset = None
    try:
        _r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                          params={"timeout": 0}, timeout=15)
        _ups = _r.json().get("result", [])
        if _ups:
            tg_offset = _ups[-1]["update_id"] + 1
    except Exception as e:
        log.warning(f"startup getUpdates drain failed: {e}")

    last_prediction = 0.0   # 0 → run one immediately on startup
    while True:
        try:
            tg_offset = poll_commands(tg_offset)     # WATCH commands (long-poll ~10s)
            if time.time() - last_prediction >= CHECK_INTERVAL:
                last_prediction = time.time()
                run_prediction(SYMBOL)
        except Exception as e:
            import traceback
            log.error(f"Cycle error: {e}\n{traceback.format_exc()}")
            tg(f"⚠️ Prediction cycle error: {e}")
        jarvis_brain.update_bot_heartbeat("lenny_predictions")
        time.sleep(1)
