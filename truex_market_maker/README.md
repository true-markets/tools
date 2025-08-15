# TrueX Market Maker

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Status](https://img.shields.io/badge/status-beta-yellow.svg)

A sophisticated automated market maker for TrueX with external data integration, advanced pricing models, and intelligent order management.

## 🚀 Key Features

### Core Market Making
- **Automated order placement** - Maintains bid/ask spreads across multiple price levels
- **Risk management** - Configurable position limits and exposure controls  
- **Sanity checks** - Validates market conditions before placing orders
- **Graceful shutdown** - Cancels all orders when stopping

### 🌟 Enhanced Features
- **External market data integration** - Real-time data from major exchanges (Coinbase, etc.)
- **Advanced pricing models** - Multiple strategies for optimal pricing
- **Intelligent order adjustments** - Reactive order management based on market movements
- **No API keys required** - Uses public endpoints for external data
- **Configurable architecture** - Easy to extend with new providers and models

## 🔓 No API Keys Required!

The external data integration works **without any Coinbase API keys**! Uses only public Coinbase Exchange endpoints for real-time market data.

## 📋 Table of Contents

- [Quick Start](#-quick-start)
- [Installation](#-installation)
- [Configuration](#️-configuration)
- [Usage](#-usage)
- [External Data Integration](#-external-data-integration)
- [Pricing Models](#-pricing-models)
- [Order Adjustment System](#-order-adjustment-system)
- [Development](#-development)
- [Troubleshooting](#-troubleshooting)

## 🚀 Quick Start

### Prerequisites

1. **Python 3.8+**
2. **TrueX Exchange access** with API credentials
3. **Network connectivity** for external data (optional but recommended)

### Environment Variables

```bash
export TRUEX_HOST="your-truex-host"
export TRUEX_REST_PORT="8080" 
export TRUEX_WS_PORT="8081"
export TRUEX_API_KEY="your-api-key"
export TRUEX_SECRET_KEY="your-secret-key"
export TRUEX_USER="your-username"
```

### Basic Usage

```bash
# Install dependencies
pip install -r requirements-external.txt

# Run basic market maker
./scripts/run BTC-PYUSD

# Run enhanced market maker with external data
./scripts/run_enhanced BTC-PYUSD
```

## 📦 Installation

### Standard Installation

```bash
git clone <repository-url>
cd truex_market_maker
pip install -r requirements-external.txt
```

### Development Installation

```bash
# Install development dependencies
make install-dev

# Install pre-commit hooks
make install-hooks

# Format and lint code
make check
```

### Quick Test

Verify external data integration:

```bash
python3 -c "
import asyncio
import aiohttp

async def test():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://api.exchange.coinbase.com/products/BTC-USD/ticker') as r:
            data = await r.json()
            print(f'BTC-USD: \${data[\"price\"]} (bid: \${data[\"bid\"]}, ask: \${data[\"ask\"]})')

asyncio.run(test())
"
```

## ⚙️ Configuration

### Basic Configuration

Create or edit `market_maker/settings.py`:

```python
# Import base settings
from market_maker._settings_base import *

# Basic market making settings
SYMBOLS = ["BTC-PYUSD", "ETH-PYUSD"]
ORDER_START_SIZE = 0.1
ORDER_STEP_SIZE = 0.1
QUOTE_SIZE = 20
RELIST_INTERVAL = 0.003

# Enable enhanced features
USE_EXTERNAL_DATA = True
USE_PRICING_MODELS = True
```

### Enhanced Configuration

```python
# External data settings
EXTERNAL_DATA_FIRST = True  # Use external data as primary source
EXTERNAL_DATA_PROVIDERS = {
    'coinbase_rest': {
        'enabled': True,
        'type': 'coinbase_rest',
        'poll_interval': 10,  # seconds
    }
}

# Symbol mapping for external providers  
EXTERNAL_SYMBOL_MAPPING = {
    'BTC-PYUSD': {'coinbase': 'BTC-USD'},
    'ETH-PYUSD': {'coinbase': 'ETH-USD'},
}

# Pricing models
PRICING_MODELS = {
    'local_aware': {
        'enabled': True,
        'priority': 60,
        'external_weight': 0.7,
        'local_weight': 0.3,
        'base_spread_bps': 40
    }
}

# Order adjustment settings
PRICE_MOVE_THRESHOLD = 0.00002  # 0.002% movement triggers adjustment
ORDER_ADJUSTMENT_COOLDOWN = 15  # seconds between adjustments
```

## 📖 Usage

### Running the Market Maker

```bash
# Basic market maker (local data only)
./scripts/run BTC-PYUSD

# Enhanced market maker (with external data)
./scripts/run_enhanced BTC-PYUSD

# Multiple symbols
./scripts/run_enhanced BTC-PYUSD ETH-PYUSD
```

### Monitoring

The enhanced market maker provides detailed status logging:

```
INFO:market_maker:🚀 === Enhanced Market Maker Configuration ===
INFO:market_maker:🌐 Data Priority: ✅ External First (with local fallback)
INFO:market_maker:🎯 Order Adjustments: ✅ Enabled
INFO:market_maker:   📊 Price threshold: 0.00002 (0.002%)
INFO:market_maker:   ⏱️ Cooldown: 15s

INFO:market_maker:📊 === Enhanced Status for BTC-PYUSD ===
INFO:market_maker:🎯 Current Order Data Source: external
INFO:market_maker:Local: 65420.0000 / 65450.0000 (mid: 65435.0000)
INFO:market_maker:External (coinbase_rest): 65415.0000 / 65425.0000 (mid: 65420.0000)
INFO:market_maker:Model (local_aware): fair=65423.5000, bid/ask=65403.5000/65443.5000, confidence=0.85
```

### Graceful Shutdown

Press `Ctrl+C` to initiate graceful shutdown:
- Cancels all open orders
- Disconnects external data providers
- Closes all connections cleanly

## 🌐 External Data Integration

### Architecture

```
market_maker/external_data/
├── base.py          # Base classes and data management
├── coinbase.py      # Coinbase REST and WebSocket providers
└── __init__.py
```

### Supported Providers

| Provider | Type | Auth Required | Real-time | Rate Limits |
|----------|------|---------------|-----------|-------------|
| Coinbase REST | HTTP Polling | ❌ No | ❌ No | 10 req/sec |
| Coinbase WebSocket | WebSocket | ❌ No | ✅ Yes | None |

### Configuration Examples

#### Conservative Setup
```python
EXTERNAL_DATA_PROVIDERS = {
    'coinbase_rest': {
        'enabled': True,
        'type': 'coinbase_rest', 
        'poll_interval': 30,  # Slower polling
    }
}

# High confidence thresholds
MIN_PRICING_CONFIDENCE = 0.8
MAX_EXTERNAL_DEVIATION = 0.05  # 5% max deviation
```

#### Aggressive Setup
```python
EXTERNAL_DATA_PROVIDERS = {
    'coinbase_ws': {
        'enabled': True,
        'type': 'coinbase_ws',  # Real-time WebSocket
    }
}

# Lower confidence thresholds
MIN_PRICING_CONFIDENCE = 0.6
MAX_EXTERNAL_DEVIATION = 0.15  # 15% max deviation
```

### Adding New Providers

1. Create a new file in `market_maker/external_data/`
2. Inherit from `ExternalDataProvider`:

```python
from .base import ExternalDataProvider

class MyProvider(ExternalDataProvider):
    def __init__(self):
        super().__init__("my_provider")
    
    async def connect(self):
        # Implement connection logic
        pass
    
    async def subscribe(self, symbol):
        # Implement subscription logic  
        pass
    
    def get_current_data(self, symbol):
        # Return current market data
        pass
```

3. Register in settings:

```python
EXTERNAL_DATA_PROVIDERS = {
    'my_provider': {
        'enabled': True,
        'type': 'my_provider',
    }
}
```

## 💰 Pricing Models

### Available Models

#### 1. Simple Spread Model
Adds fixed spread to external reference price.

```python
'simple_spread': {
    'enabled': True,
    'priority': 100,
    'spread_bps': 50,           # 5 basis points spread
    'reference_provider': 'coinbase_rest'
}
```

**Use Case:** Simple external price tracking with consistent spreads.

#### 2. Consensus Model
Takes consensus across multiple external providers, removing outliers.

```python
'consensus': {
    'enabled': True,
    'priority': 80,
    'min_providers': 2,         # Minimum providers required
    'outlier_threshold': 0.02,  # 2% outlier threshold
    'base_spread_bps': 30
}
```

**Use Case:** High confidence pricing when multiple data sources are available.

#### 3. Local Market Aware Model ⭐ **Recommended**
Weights external data with local market conditions.

```python
'local_aware': {
    'enabled': True,
    'priority': 60,
    'external_weight': 0.7,     # 70% weight to external data
    'local_weight': 0.3,        # 30% weight to local market
    'base_spread_bps': 40
}
```

**Use Case:** Best of both worlds - external price discovery with local market awareness.

#### 4. Momentum Model
Considers price momentum and volatility for dynamic pricing.

```python
'momentum': {
    'enabled': True,
    'priority': 90,
    'lookback_minutes': 15,     # Price history window
    'momentum_factor': 0.1,     # Momentum adjustment factor
    'base_spread_bps': 35
}
```

**Use Case:** Trending markets where momentum is important.

### Adding Custom Models

1. Create a class inheriting from `PricingModel`:

```python
from .base import PricingModel, PricingResult

class MyModel(PricingModel):
    def calculate_price(self, symbol, external_data, local_ticker):
        # Implement pricing logic
        return PricingResult(
            symbol=symbol,
            fair_value=calculated_fair,
            bid_price=calculated_bid,
            ask_price=calculated_ask,
            confidence=calculated_confidence,
            spread_bps=calculated_spread,
            model_name="my_model"
        )
```

2. Register in settings and initialization code.

## 🎯 Order Adjustment System

### How It Works

The order adjustment system monitors for:

1. **Price movements** exceeding thresholds (default: 0.002%)
2. **External market changes** that affect pricing models
3. **Order age** beyond maximum limits
4. **Confidence drops** in pricing models

### Key Features

- **Reactive adjustments** - Responds immediately to significant price movements
- **Integrated with converge_orders** - No conflicts between order management systems  
- **Cooldown periods** - Prevents excessive order churning
- **Hysteresis** - Prevents oscillation between prices
- **Batch processing** - Efficient handling of multiple order adjustments

### Configuration

```python
# Price movement threshold that triggers reactive adjustments
PRICE_MOVE_THRESHOLD = 0.00002  # 0.002%

# Cooldown between adjustment cycles per symbol
ORDER_ADJUSTMENT_COOLDOWN = 15  # seconds

# Maximum order age before forced adjustment
MAX_ORDER_AGE_SECONDS = 300  # 5 minutes

# Minimum confidence required for pricing model use
MIN_PRICING_CONFIDENCE = 0.5
```

### Monitoring Adjustments

```
🚨 Significant price movement detected for BTC-PYUSD - triggering reactive adjustments
🚨 Using reactive threshold 0.00002 for BTC-PYUSD
📝 Amending order 12345: pricing adjustment (Δ0.012%, reactive threshold: 0.00002)
Amending BUY: 0.1 @ 48500.00 to 0.1 @ 48506.00 (+6.00)
```

## 🛡️ Safety Features

1. **Fallback to local pricing** - If external data fails or confidence is low
2. **Maximum deviation limits** - Prevents extreme price deviations from local market
3. **Cooldown periods** - Prevents rapid order churning
4. **Confidence thresholds** - Only uses high-confidence external pricing
5. **Position limits** - Original position management still applies
6. **Graceful shutdown** - Proper cleanup of all connections and orders
7. **Sanity checks** - Validates market conditions before placing orders

## 🔧 Development

### Project Structure

```
truex_market_maker/
├── market_maker/
│   ├── external_data/      # External data providers
│   ├── pricing/           # Pricing models
│   ├── rest/             # REST API client
│   ├── utils/            # Utility functions
│   ├── ws/               # WebSocket handling
│   ├── market_maker.py   # Main market maker logic
│   ├── order_adjustment.py  # Order adjustment engine
│   └── settings.py       # Configuration
├── scripts/
│   ├── run              # Basic market maker launcher
│   └── run_enhanced     # Enhanced market maker launcher
├── test/               # Test files
├── requirements-external.txt
├── pyproject.toml      # Build configuration
└── Makefile           # Development tasks
```

### Development Workflow

```bash
# Format code
make format

# Run linting 
make lint

# Run both formatting and linting
make check

# Run tests (when available)
make test

# Clean temporary files
make clean
```

### Code Quality

- **Black** for code formatting (88 character line length)
- **isort** for import sorting
- **flake8** for linting
- **mypy** for type checking
- **pre-commit** hooks for automated quality checks

## 🐛 Troubleshooting

### Common Issues

#### No External Data
```
❌ No external data available for BTC-PYUSD yet (providers may still be connecting)
```
**Solution:** Check network connectivity and wait for provider connection. External data typically takes 10-30 seconds to initialize.

#### Low Confidence Pricing
```
⚠️ Low confidence pricing for BTC-PYUSD: 0.3 < 0.5 threshold
```
**Solution:** Check if external providers are working properly or lower `MIN_PRICING_CONFIDENCE`.

#### Excessive Adjustments
```
🎯 Order adjustment cooldown active for BTC-PYUSD (8s remaining)
```
**Solution:** Increase `ORDER_ADJUSTMENT_COOLDOWN` or `PRICE_MOVE_THRESHOLD` to reduce sensitivity.

#### Price Deviations
```
❌ External spread too wide for BTC-PYUSD: 5.2% > 2% limit
```
**Solution:** Increase `EXTERNAL_DATA_MAX_SPREAD_PCT` or check provider data quality.

### Debug Mode

Enable detailed logging in settings:

```python
import logging
LOG_LEVEL = logging.DEBUG
```

This provides detailed information about:
- External data provider connections
- Pricing model calculations  
- Order adjustment decisions
- Market data validation

### Performance Considerations

- **External data** runs in separate threads to avoid blocking main trading loop
- **REST polling intervals** should balance data freshness with API rate limits  
- **WebSocket connections** provide real-time data but require more complex error handling
- **Pricing models** cache results to avoid redundant calculations
- **Order adjustments** use batch processing to minimize API calls

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📞 Support

For support and questions:
- Check the troubleshooting section above
- Review configuration examples in `example_external_config.py`
- Enable debug logging for detailed diagnostics

---

**🚀 Ready to run your enhanced TrueX Market Maker with external data integration!**
