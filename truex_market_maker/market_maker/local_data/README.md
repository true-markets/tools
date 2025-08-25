# Local Data Provider Architecture

This directory contains the consolidated TrueX local market data provider that uses the same interface as external data providers.

## Architecture Overview

### Before (Scattered Components)
```
market_maker/
├── rest/client.py          # TruexRESTClient class
├── ws/processor.py         # TruexWebsocket class  
└── truex.py               # TrueX connector (combined REST + WS)
```

### After (Consolidated Interface)
```
market_maker/
└── local_data/
    ├── __init__.py        # Exports TruexDataProvider and TrueX
    ├── truex.py          # Unified provider + adapter in single file
    ├── test_interface.py # Interface validation tests
    └── README.md         # This file
```

## Key Benefits

### 1. **Unified Interface**
- Local data now implements the same `ExternalDataProvider` interface as external providers
- Consistent async methods: `connect()`, `subscribe()`, `get_current_data()`
- Standardized `MarketData` format for all data sources

### 2. **Backward Compatibility**
- `truex.py` acts as an adapter maintaining existing API
- All existing method calls continue to work unchanged
- No changes required in `market_maker.py` or other components

### 3. **Consolidated Architecture**
- Single `TruexDataProvider` class combines REST and WebSocket functionality
- Eliminates code duplication between REST and WebSocket handling
- Easier maintenance and testing

### 4. **Consistent Data Flow**
- Local data can now be treated the same as external data
- Pricing models can work with local data using the same interface
- Potential for local data to participate in external data consensus models

## TruexDataProvider Features

### External Data Provider Interface
```python
from market_maker.local_data import TruexDataProvider

provider = TruexDataProvider()
await provider.connect()
await provider.subscribe("BTC-PYUSD")
market_data = await provider.get_current_data("BTC-PYUSD")
```

### TrueX Interface
```python
# Direct import from consolidated location
from market_maker.local_data import TrueX
# Or explicit import
from market_maker.local_data.truex import TrueX

truex = TrueX(rest_url=..., ws_url=...)
truex.Instrument("BTC-PYUSD")  # Subscribes via provider.subscribe()
ticker = truex.Ticker("BTC-PYUSD")  # Returns local ticker data
orders = truex.OpenOrders()  # Returns open orders via provider
```

### Combined Functionality
- **REST API**: Order management, balances, instrument data
- **WebSocket**: Real-time market data, position updates, trade notifications  
- **Authentication**: Unified auth handling for both REST and WebSocket
- **Error Handling**: Consistent error handling and logging
- **Connection Management**: Automatic reconnection and graceful shutdown

## Integration Points

### With External Data System
```python
from market_maker.external_data.base import ExternalDataManager
from market_maker.local_data import TruexDataProvider

# Local data can now be managed alongside external providers
manager = ExternalDataManager()
local_provider = TruexDataProvider()
manager.add_provider(local_provider)

# Treat local data like any other provider
all_data = manager.get_all_data("BTC-PYUSD")
best_prices = manager.get_best_bid_ask("BTC-PYUSD")
```

### With Market Maker
The market maker continues to use the same interface through `truex.py`, but now benefits from:
- Better error handling and logging
- Unified data structures  
- Potential for local data integration with pricing models
- Consistent async handling

## Migration Notes

### For Developers  
- Import paths updated to: `from market_maker.local_data.truex import TrueX`
- All existing method signatures are preserved
- Clean consolidated architecture with no legacy files
- Single location for all TrueX functionality

### For Future Enhancements  
- Local data can now participate in pricing model consensus
- Easy to add local data to external data managers
- Consistent interface makes testing simpler
- Unified logging and monitoring

## Files Reference

| File | Purpose |
|------|---------|
| `truex_provider.py` | Main unified provider implementing `ExternalDataProvider` |
| `rest_client_legacy.py` | Original REST client for reference |
| `ws_processor_legacy.py` | Original WebSocket processor for reference |
| `rest_init_legacy.py` | Original REST package init |
| `ws_init_legacy.py` | Original WebSocket package init |

The legacy files are kept for reference and can be removed once the refactoring is fully verified in production.
