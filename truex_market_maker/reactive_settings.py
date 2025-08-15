"""
Highly reactive settings for small price movements.

Add this to your settings.py for maximum responsiveness:

from reactive_settings import *
"""

# Ultra-responsive external pricing
MIN_PRICING_CONFIDENCE = 0.05  # Accept almost any external pricing
MAX_EXTERNAL_DEVIATION = 1.0  # Allow up to 100% deviation

# Very frequent adjustments
ORDER_ADJUSTMENT_INTERVAL = 2  # Check every 2 seconds
PRICE_MOVE_THRESHOLD = 0.001  # 0.1% threshold (very sensitive)
ORDER_ADJUSTMENT_COOLDOWN = 10  # Only 10 seconds between adjustments

# Faster pricing model updates
PRICING_MODELS = {
    "momentum": {
        "enabled": True,
        "priority": 70,
        "config": {
            "lookback_periods": 5,  # Shorter lookback for faster reaction
            "momentum_weight": 0.8,  # Strong momentum weighting
            "volatility_adjustment": True,
            "base_spread_bps": 20,  # Tighter spread
        },
    },
    "local_aware": {
        "enabled": True,
        "priority": 80,
        "config": {
            "external_weight": 0.95,  # 95% weight to external data
            "local_weight": 0.05,  # 5% weight to local market
            "base_spread_bps": 25,  # Tighter spread
        },
    },
}

print("🚀 REACTIVE MODE ENABLED!")
print("   ⚡ 2-second adjustment intervals")
print("   🎯 0.1% price movement threshold")
print("   📈 95% external price weighting")
print("   💨 Ultra-low confidence requirements")
print("")
print("⚠️  WARNING: This is very aggressive!")
print("💡 Monitor closely and adjust if needed")
