import sys
sys.path.insert(0, '/root/jarvis')
from jarvis_context import get_context

class BotOrchestrator:
    def __init__(self):
        self.ctx = get_context()

    def get_macro_regime(self):
        regime = self.ctx.get_context('macro_regime')
        return regime or 'RISK_OFF'

    def get_macro_multiplier(self):
        mult = self.ctx.get_context('macro_multiplier')
        return float(mult) if mult else 0.5

    def can_buy(self, symbol, strategy):
        regime = self.get_macro_regime()
        if regime == 'RISK_OFF':
            return False, f"Regime {regime} -- no buys"
        if regime == 'NORMAL' and strategy == 'aggressive':
            return False, "Aggressive blocked in NORMAL"
        return True, "OK"

    def size_position(self, base_size, symbol, strategy):
        mult = self.get_macro_multiplier()
        return base_size * mult

def get_orchestrator():
    return BotOrchestrator()
