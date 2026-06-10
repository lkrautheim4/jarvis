#!/usr/bin/env python3
"""DEPRECATED — superseded by options_grader.py.

This script used to close paper trades at expiry using *intrinsic value*
(max(0, strike - spot)), which diverged from options_grader.py's live-premium
±50%/DTE/expiry logic and produced conflicting exit_price/pnl on the same
trade. options_grader is now the single source of truth for closing paper
trades, so this no longer writes anything — it is a read-only no-op kept so
any cron/caller doesn't error. Use options_grader.py instead.
"""
import sys
sys.path.insert(0, '/root/jarvis')

def grade_paper_trades():
    print("grade_paper_trades.py is DEPRECATED and does nothing — "
          "options_grader.py is the single source of truth for closing paper trades.")
    return 0

if __name__ == '__main__':
    grade_paper_trades()
