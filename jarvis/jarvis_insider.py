#!/usr/bin/env python3
"""
jarvis_insider.py — REAL insider-PURCHASE scanner for JARVIS.

Fixes every bug found in the old fetch_insider_filings():
  - OLD query searched the text phrase "transaction code P" -> never matched. NEW
    searches Form 4s by issuer and verifies the code in the filing XML.
  - OLD matched tickers as substrings of ANY display_name (so "GS" matched every
    Goldman filing about other companies). NEW matches against the issuer's actual
    <issuerTradingSymbol> from the Form 4 XML — authoritative, no false positives.
  - OLD read dead keys entity_name / period_of_report (always blank). NEW reads
    real fields and pulls issuer/owner/shares/price from the XML.
  - OLD labeled everything "verified insider PURCHASE" without checking buy vs sell.
    NEW only returns transactions with code P (open-market purchase).

Run LIVE on the VPS (sec.gov reachable there) to prove it before wiring in:
    cd /root/jarvis && python3 jarvis_insider.py AAPL TSLA GS NVDA
Offline self-test of the XML parser (no network):
    python3 jarvis_insider.py --selftest
"""
import sys, time, requests
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

UA = "Lenny Krautheim lkrautheim4@gmail.com"  # EDGAR requires a descriptive User-Agent
EFTS = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def _get(url, **kw):
    return requests.get(url, headers={"User-Agent": UA}, timeout=20, **kw)


def search_form4(query, startdt, enddt):
    """Full-text search Form 4s for an issuer name/ticker in a date window."""
    r = _get(EFTS, params={"q": f'"{query}"', "forms": "4",
                           "startdt": startdt, "enddt": enddt})
    r.raise_for_status()
    return r.json().get("hits", {}).get("hits", [])


def _parse_id(hit):
    # _id like "0000886982-26-000291:wk-form4_1781738345.xml"
    acc, _, fname = hit["_id"].partition(":")
    return acc, fname


def fetch_form4_xml(ciks, accession, filename):
    """Try each associated CIK until the archive path resolves; return XML text + url."""
    acc_nodash = accession.replace("-", "")
    for cik in ciks:
        try:
            cik_int = str(int(cik))  # strip leading zeros
        except (TypeError, ValueError):
            continue
        url = f"{ARCHIVES}/{cik_int}/{acc_nodash}/{filename}"
        try:
            r = _get(url)
            if r.status_code == 200 and "<ownershipDocument" in r.text:
                return r.text, url
        except Exception:
            pass
        time.sleep(0.15)  # EDGAR politeness
    return None, None


def extract_purchases(xml_text):
    """Return issuer info + ONLY non-derivative transactions with code P (purchase)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    issuer_name = (root.findtext(".//issuer/issuerName") or "").strip()
    issuer_ticker = (root.findtext(".//issuer/issuerTradingSymbol") or "").strip().upper()
    owner = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    purchases = []
    for t in root.findall(".//nonDerivativeTransaction"):
        code = (t.findtext(".//transactionCoding/transactionCode") or "").strip().upper()
        if code != "P":
            continue  # P = open-market purchase. Skips S (sale), A (grant), etc.
        shares = t.findtext(".//transactionAmounts/transactionShares/value") or ""
        price = t.findtext(".//transactionAmounts/transactionPricePerShare/value") or ""
        date = t.findtext(".//transactionDate/value") or ""
        purchases.append({"shares": shares, "price": price, "date": date})
    return {"issuer": issuer_name, "ticker": issuer_ticker,
            "owner": owner, "purchases": purchases}


def scan(watch_tickers, days=3):
    """For each watched ticker, find Form 4s where that ticker is the ISSUER and a
    real open-market PURCHASE (code P) occurred. Returns clean alert records."""
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    seen = set()
    alerts = []
    for ticker in watch_tickers:
        try:
            hits = search_form4(ticker, start, today)
        except Exception as e:
            print(f"  [{ticker}] search error: {e}", file=sys.stderr)
            continue
        for hit in hits[:20]:
            acc, fname = _parse_id(hit)
            if acc in seen:
                continue
            ciks = hit.get("_source", {}).get("ciks", [])
            xml_text, url = fetch_form4_xml(ciks, acc, fname)
            if not xml_text:
                continue
            info = extract_purchases(xml_text)
            if not info or not info["purchases"]:
                continue
            # AUTHORITATIVE match: the watched ticker must be the ISSUER's symbol.
            if info["ticker"] != ticker.upper():
                continue
            seen.add(acc)
            alerts.append({
                "ticker": ticker.upper(), "issuer": info["issuer"],
                "owner": info["owner"], "purchases": info["purchases"],
                "filed": hit.get("_source", {}).get("file_date", ""),
                "accession": acc, "url": url, "source": "SEC EDGAR Form 4 (code P verified)",
                "ts": datetime.now().isoformat(),
            })
            time.sleep(0.15)
    return alerts


# ---- bot integration: send ONLY new purchases, never re-alert -------------
import json, os

STATE_FILE = "/root/jarvis/insider_state.json"


def _load_state(path):
    try:
        with open(path) as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_state(path, s):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(s), f)
    os.replace(tmp, path)  # atomic


def format_alert(a):
    lines = [f"📋 {a['ticker']} — {a['issuer']}", f"  {a['owner']}"]
    for p in a["purchases"]:
        lines.append(f"  Bought {p.get('shares','?')} sh @ ${p.get('price','?')} on {p.get('date','?')}")
    lines.append(f"  Filed {a['filed']} · code P (open-market purchase, verified)")
    lines.append(f"  {a['url']}")
    return "\n".join(lines)


def scan_and_alert(tg, watch_tickers, days=3, state_file=STATE_FILE):
    """Scan for NEW code-P purchases; send one tg() alert each. Silent if none new.
    Dedup is by accession number stored in state_file, so the same filing never
    re-alerts on subsequent polls."""
    seen = _load_state(state_file)
    new = [a for a in scan(watch_tickers, days=days) if a["accession"] not in seen]
    if not new:
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    header = f"🟢 INSIDER PURCHASE ALERT ({today})\nSource: SEC EDGAR Form 4 — code P verified\n"
    for a in new:
        tg(header + "\n" + format_alert(a))
        seen.add(a["accession"])
    _save_state(state_file, seen)
    return len(new)


# ---- offline parser self-test (no network) --------------------------------
_SAMPLE = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerName>ETSY INC</issuerName><issuerTradingSymbol>ETSY</issuerTradingSymbol></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerName>BURNS M MICHELE</rptOwnerName></reportingOwnerId></reportingOwner>
  <nonDerivativeTransaction>
    <transactionDate><value>2026-06-09</value></transactionDate>
    <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
    <transactionAmounts>
      <transactionShares><value>1000</value></transactionShares>
      <transactionPricePerShare><value>52.30</value></transactionPricePerShare>
    </transactionAmounts>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction>
    <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
    <transactionAmounts><transactionShares><value>500</value></transactionShares></transactionAmounts>
  </nonDerivativeTransaction>
</ownershipDocument>"""


def _selftest():
    info = extract_purchases(_SAMPLE)
    print("issuer:", info["issuer"], "| ticker:", info["ticker"], "| owner:", info["owner"])
    print("purchases:", info["purchases"])
    assert info["ticker"] == "ETSY"
    assert len(info["purchases"]) == 1, "must keep only the P, drop the S"
    assert info["purchases"][0]["shares"] == "1000"
    assert info["purchases"][0]["price"] == "52.30"
    # issuer match logic
    assert info["ticker"] == "ETSY".upper()
    print("\nSELF TEST PASSED — parser keeps code P, drops code S, reads issuer ticker")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        tickers = [a for a in sys.argv[1:] if not a.startswith("-")] or ["AAPL", "TSLA", "NVDA"]
        print(f"Scanning Form 4 PURCHASES (code P) for {tickers} over last 3 days...\n")
        results = scan(tickers)
        if not results:
            print("No verified insider purchases found in window. "
                  "(This is the HONEST answer — most days have none for a given watchlist.)")
        for a in results:
            print(f"✅ {a['ticker']} — {a['owner']} bought, filed {a['filed']}")
            for p in a["purchases"]:
                print(f"     {p['shares']} sh @ ${p['price']} on {p['date']}")
            print(f"     {a['url']}")
