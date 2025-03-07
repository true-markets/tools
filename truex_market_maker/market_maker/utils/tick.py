
from market_maker.utils.constants import QUOTE_TICK_BREAKPOINTS

def GetQuoteTick(price):
     """Get the tick size for a given price."""
     for price_level, tick in QUOTE_TICK_BREAKPOINTS.items():
         if price < price_level:
             return tick
     return 0.50
