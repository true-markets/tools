"""
Example configuration for external data integration.

Copy relevant sections to your settings.py file to enable external data features.
"""

# ========================================
# External Market Data Configuration
# ========================================

# Enable external market data integration
USE_EXTERNAL_DATA = True

# External data providers to use
EXTERNAL_DATA_PROVIDERS = {
    # Coinbase REST API (recommended for getting started)
    "coinbase_rest": {
        "enabled": True,
        "type": "coinbase_rest",
        "poll_interval": 15,  # Poll every 15 seconds
    },
    # Coinbase WebSocket (for real-time data)
    "coinbase_ws": {
        "enabled": False,  # Set to True for real-time updates
        "type": "coinbase_ws",
    },
}

# Symbol mapping for external providers
# Maps your local symbols to external provider symbols
EXTERNAL_SYMBOL_MAPPING = {
    "BTC-PYUSD": {
        "coinbase": "BTC-USD",  # PYUSD not widely available, use USD as proxy
    },
    "ETH-PYUSD": {
        "coinbase": "ETH-USD",  # PYUSD not widely available, use USD as proxy
    },
    # Add more symbols as needed
    "BTC-USD": {
        "coinbase": "BTC-USD",
    },
    "ETH-USD": {
        "coinbase": "ETH-USD",
    },
}

# ========================================
# Pricing Models Configuration
# ========================================

# Enable external pricing models
USE_PRICING_MODELS = True

# Default pricing model if none specified
DEFAULT_PRICING_MODEL = "local_aware"

# Available pricing models and their configurations
PRICING_MODELS = {
    # Simple spread model - good for testing
    "simple_spread": {
        "enabled": True,
        "priority": 100,  # Lower number = higher priority
        "config": {
            "spread_bps": 60,  # 6 basis points spread (0.6%)
            "reference_provider": "coinbase_rest",
        },
    },
    # Consensus model - requires multiple providers
    "consensus": {
        "enabled": False,  # Enable when you have multiple providers
        "priority": 80,
        "config": {
            "min_providers": 2,
            "outlier_threshold": 0.015,  # 1.5% outlier threshold
            "base_spread_bps": 40,
        },
    },
    # Local market aware model - RECOMMENDED
    "local_aware": {
        "enabled": True,
        "priority": 60,  # Highest priority (lowest number)
        "config": {
            "external_weight": 0.75,  # 75% weight to external data
            "local_weight": 0.25,  # 25% weight to local market
            "base_spread_bps": 50,  # 5 basis points base spread
        },
    },
    # Momentum model - for trending markets
    "momentum": {
        "enabled": True,
        "priority": 90,
        "config": {
            "lookback_minutes": 20,  # Look back 20 minutes for momentum
            "momentum_factor": 0.05,  # Small momentum adjustment
            "base_spread_bps": 45,
        },
    },
}

# ========================================
# Pricing Safety Configuration
# ========================================

# Pricing model selection strategy
# 'best_confidence': Use model with highest confidence
# 'preferred': Use highest priority model that has sufficient data
PRICING_SELECTION_STRATEGY = "best_confidence"

# Minimum confidence required to use external pricing
MIN_PRICING_CONFIDENCE = 0.6  # 60% confidence threshold

# Fallback to local pricing if external fails
FALLBACK_TO_LOCAL_PRICING = True

# Maximum deviation from local price allowed (as percentage)
# If external price deviates more than this from local, fall back to local
MAX_EXTERNAL_DEVIATION = 0.08  # 8% maximum deviation

# ========================================
# Order Adjustment Configuration
# ========================================

# Price movement threshold for order adjustments
PRICE_MOVE_THRESHOLD = 0.008  # 0.8% - adjust orders if price moves more than this

# Maximum age for orders before considering cancellation
MAX_ORDER_AGE_SECONDS = 400  # 6.7 minutes

# Cooldown period between order adjustments
ORDER_ADJUSTMENT_COOLDOWN = 45  # 45 seconds between adjustments

# ========================================
# Conservative Configuration Example
# ========================================

# For a more conservative setup, use these values instead:
"""
USE_EXTERNAL_DATA = True
USE_PRICING_MODELS = True

# Only enable simple and local-aware models
PRICING_MODELS = {
    'local_aware': {
        'enabled': True,
        'priority': 60,
        'config': {
            'external_weight': 0.6,     # Less weight to external (60%)
            'local_weight': 0.4,        # More weight to local (40%)
            'base_spread_bps': 70       # Wider spread for safety
        }
    }
}

# Higher confidence threshold
MIN_PRICING_CONFIDENCE = 0.8

# Smaller deviation allowed
MAX_EXTERNAL_DEVIATION = 0.05  # 5%

# Less frequent adjustments
PRICE_MOVE_THRESHOLD = 0.015  # 1.5%
ORDER_ADJUSTMENT_COOLDOWN = 90  # 90 seconds
"""

# ========================================
# Aggressive Configuration Example
# ========================================

# For a more aggressive setup with multiple models:
"""
USE_EXTERNAL_DATA = True
USE_PRICING_MODELS = True

# Enable all models
PRICING_MODELS = {
    'momentum': {
        'enabled': True,
        'priority': 50,
        'config': {
            'lookback_minutes': 10,
            'momentum_factor': 0.15,    # Higher momentum factor
            'base_spread_bps': 30       # Tighter spread
        }
    },
    'local_aware': {
        'enabled': True,
        'priority': 60,
        'config': {
            'external_weight': 0.85,    # Heavy weight to external
            'local_weight': 0.15,
            'base_spread_bps': 25       # Very tight spread
        }
    }
}

# Lower confidence threshold
MIN_PRICING_CONFIDENCE = 0.4

# Allow larger deviations
MAX_EXTERNAL_DEVIATION = 0.15  # 15%

# More frequent adjustments
PRICE_MOVE_THRESHOLD = 0.005   # 0.5%
ORDER_ADJUSTMENT_COOLDOWN = 20  # 20 seconds
"""
