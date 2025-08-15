import logging
from os import environ
from os.path import join

########################################################################################################################
# Connection/Auth
########################################################################################################################

# API URL.
HOST = environ["TRUEX_HOST"]
REST_PORT = environ["TRUEX_REST_PORT"]
BASE_REST_URL = f"http://{HOST}:{REST_PORT}/api/v1"
WS_PORT = environ["TRUEX_WS_PORT"]
BASE_WS_URL = f"ws://{HOST}:{WS_PORT}"

# The TrueX API requires permanent API keys.
API_KEY = environ["TRUEX_API_KEY"]
API_SECRET = environ["TRUEX_SECRET_KEY"]
API_USER = environ["TRUEX_USER"]

########################################################################################################################
# Target
########################################################################################################################

# Instruments to market make on TrueX.
SYMBOLS = ["BTC-PYUSD"]

########################################################################################################################
# TICK SIZE
########################################################################################################################

TICK_SIZE = 0.50
QUOTE_SIZE = 0.0001

########################################################################################################################
# Order Size & Spread
########################################################################################################################

# How many pairs of buy/sell orders to keep open
ORDER_PAIRS = 6

# ORDER_START_SIZE will be the number of contracts submitted on level 1
# Number of contracts from level 1 to ORDER_PAIRS - 1 will follow the function
# [ORDER_START_SIZE + ORDER_STEP_SIZE (Level -1)]
# ORDER_START_SIZE and ORDER_STEP_SIZE must be multiplication of instrument lot size.
ORDER_START_SIZE = 0.1
ORDER_STEP_SIZE = 0.1

# Distance between successive orders, as a percentage (example: 0.005 for 0.5%)
INTERVAL = 0.01

# Minimum spread to maintain, in percent, between asks & bids (example: 0.01 for 1%)
MIN_SPREAD = 0.005

# If True, market-maker will place orders just inside the existing spread and work the interval % outwards,
# rather than starting in the middle and killing potentially profitable spreads.
MAINTAIN_SPREADS = True

# This number defines far much the price of an existing order can be from a desired order before it is amended.
# This is useful for avoiding unnecessary calls and maintaining your ratelimits.
#
# Further information:
# Each order is designed to be (INTERVAL*n)% away from the spread.
# If the spread changes and the order has moved outside its bound defined as
# abs((desired_order['price'] / order['price']) - 1) > settings.RELIST_INTERVAL)
# it will be resubmitted.
#
# 0.01 == 1%
RELIST_INTERVAL = 0.01

########################################################################################################################
# Trading Behavior
########################################################################################################################

# Position limits - set to True to activate. Values are in contracts.
# If you exceed a position limit, the bot will log and stop quoting that side.
CHECK_POSITION_LIMITS = False
MIN_POSITION = -10000
MAX_POSITION = 10000

# If True, will only send orders that rest in the book (ExecInst: ParticipateDoNotInitiate).
# Use to guarantee a maker rebate.
# However -- orders that would have matched immediately will instead cancel, and you may end up with
# unexpected delta. Be careful.
POST_ONLY = False

# If true, cancel any open orders from previous runs first
CANCEL_ORDERS_ON_START = False

# If True, cancel all open orders on exit (RECOMMENDED!)
# This ensures you don't leave orders hanging when the bot stops
CANCEL_ORDERS_ON_EXIT = True

########################################################################################################################
# Misc Behavior, Technicals
########################################################################################################################

# If true, don't set up any orders, just say what we would do
# DRY_RUN = True
DRY_RUN = False

# How often to re-check and replace orders.
# Generally, it's safe to make this short because we're fetching from websockets. But if too many
# order amend/replaces are done, you may hit a ratelimit. If so, email TrueX if you feel you need a higher limit.
LOOP_INTERVAL = 5

# Wait times between orders / errors
API_REST_INTERVAL = 1
API_ERROR_INTERVAL = 10
TIMEOUT = 7

# If we're doing a dry run, use these numbers for BTC balances
DRY_BTC = 50

# Available levels: logging.(DEBUG|INFO|WARN|ERROR)
LOG_LEVEL = logging.INFO

# To uniquely identify orders placed by this bot, the bot sends a ClOrdID (Client order ID) that is attached
# to each order so its source can be identified. This keeps the market maker from cancelling orders that are
# manually placed, or orders placed by another bot.
#
# If you are running multiple bots on the same symbol, give them unique ORDERID_PREFIXes - otherwise they will
# cancel each others' orders.
# Max length is 18 characters.
ORDERID_PREFIX = "mm-trx-"

# If any of these files (and this file) changes, reload the bot.
WATCHED_FILES = [
    join("market_maker", "market_maker.py"),
    join("market_maker", "truex.py"),
    join("market_maker", "settings.py"),
]


########################################################################################################################
# External Market Data
########################################################################################################################

# Enable external market data integration
USE_EXTERNAL_DATA = True

# External data providers to use
EXTERNAL_DATA_PROVIDERS = {
    "coinbase_rest": {
        "enabled": True,
        "type": "coinbase_rest",
        "poll_interval": 10,  # seconds
    },
    "coinbase_ws": {
        "enabled": False,  # Websocket provider - more complex setup
        "type": "coinbase_ws",
    },
}

# Symbol mapping for external providers
# Maps local symbols to external symbols
EXTERNAL_SYMBOL_MAPPING = {
    "BTC-PYUSD": {
        "coinbase": "BTC-USD",  # PYUSD not widely available, use USD as proxy
    },
    "ETH-PYUSD": {
        "coinbase": "ETH-USD",  # PYUSD not widely available, use USD as proxy
    },
}

########################################################################################################################
# Pricing Models
########################################################################################################################

# Enable external pricing models
USE_PRICING_MODELS = True

# Default pricing model if none specified
DEFAULT_PRICING_MODEL = "local_aware"

# Available pricing models and their configurations
PRICING_MODELS = {
    "simple_spread": {
        "enabled": True,
        "priority": 100,  # Lower number = higher priority
        "config": {"spread_bps": 50, "reference_provider": "coinbase_rest"},
    },
    "consensus": {
        "enabled": True,
        "priority": 80,
        "config": {
            "min_providers": 2,
            "outlier_threshold": 0.02,
            "base_spread_bps": 30,
        },
    },
    "local_aware": {
        "enabled": True,
        "priority": 60,  # Highest priority (lowest number)
        "config": {"external_weight": 0.7, "local_weight": 0.3, "base_spread_bps": 40},
    },
    "momentum": {
        "enabled": True,
        "priority": 90,
        "config": {
            "lookback_minutes": 15,
            "momentum_factor": 0.1,
            "base_spread_bps": 20,
        },
    },
}

# Pricing model selection strategy
# 'best_confidence': Use model with highest confidence
# 'preferred': Use highest priority model that has sufficient data
PRICING_SELECTION_STRATEGY = "best_confidence"

# Minimum confidence required to use external pricing
MIN_PRICING_CONFIDENCE = (
    0.05  # Very low threshold for high reactivity to external pricing
)

# Fallback to local pricing if external fails
FALLBACK_TO_LOCAL_PRICING = True

# Maximum deviation from local price allowed (as percentage)
# If external price deviates more than this from local, fall back to local
MAX_EXTERNAL_DEVIATION = 0.05  # increased for better external price adoption

# Bootstrap mode - use external pricing even with large deviations
# Set this to True if your local prices are very different from external markets
# and you want to gradually align with external pricing
BOOTSTRAP_TO_EXTERNAL = True

# Bootstrap settings for handling market inconsistencies
BOOTSTRAP_SPREAD_BUFFER = 0.001  # 0.1% buffer when bootstrapping from external data
STARTUP_GRACE_PERIOD = 60  # Seconds to be tolerant of market inconsistencies on startup
MAX_SANITY_FAILURES = 3  # Maximum sanity check failures before shutdown

# External Data First Mode - prioritize external data over local data
# When True, uses external market data as primary source and falls back to local only when external is unavailable
# When False, uses traditional local-first approach with external as enhancement
EXTERNAL_DATA_FIRST = True

# Minimum external data age tolerance (seconds)
# External data older than this will be considered stale and local data will be used instead
MAX_EXTERNAL_DATA_AGE = 30

# External data quality requirements for external-first mode
EXTERNAL_DATA_MIN_PROVIDERS = 1  # Minimum number of external providers required
EXTERNAL_DATA_MAX_SPREAD_PCT = (
    2.0  # Maximum spread % to consider external data valid (e.g., 2.0 = 2%)
)
EXTERNAL_DATA_STARTUP_WAIT = (
    10  # Maximum seconds to wait for external data during startup
)

########################################################################################################################
# Order Adjustment Configuration
########################################################################################################################

# How often to check for order adjustments (seconds)
ORDER_ADJUSTMENT_INTERVAL = 3  # More frequent checks for better reactivity

# Price movement threshold for order adjustments
PRICE_MOVE_THRESHOLD = 0.00002  # more reactive to smaller price moves

# Maximum age for orders before considering cancellation (seconds)
MAX_ORDER_AGE_SECONDS = 300  # 5 minutes

# Cooldown period between order adjustments for the same symbol (seconds)
ORDER_ADJUSTMENT_COOLDOWN = 15  # Shorter cooldown for more frequent adjustments

########################################################################################################################
# TrueX Portfolio
########################################################################################################################

# Specify the ticker that you hold. These will be used in portfolio calculations.
CONTRACTS = ["BTC-PYUSD"]
