#!/usr/bin/env python3
"""
Test script to verify the TruexDataProvider interface works correctly.

This is a simple test that doesn't require TrueX credentials to run.
It tests the interface structure and basic functionality.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Mock settings to avoid environment variable requirements
class MockSettings:
    BASE_REST_URL = "http://mock-api.test:8080"
    BASE_WS_URL = "ws://mock-ws.test:8081"
    API_KEY = "mock-key"
    API_SECRET = "mock-secret"
    API_USER = "mock-user"
    API_REST_TIMEOUT = 30
    LOG_LEVEL = "INFO"
    ORDERID_PREFIX = "test-"
    POST_ONLY = False

# Inject mock settings before importing modules that need them
sys.modules['market_maker.settings'] = type('MockModule', (), {'settings': MockSettings})()

# Mock constants
class MockConstants:
    VERSION = "1.0.0-test"

# Create a mock module with constants
mock_constants_module = type('MockModule', (), {})()
mock_constants_module.VERSION = "1.0.0-test"
sys.modules['market_maker.utils.constants'] = mock_constants_module

def test_interface_structure():
    """Test that the interface structure is correct."""
    print("🔍 Testing interface structure...")
    
    try:
        from market_maker.local_data.truex import TruexDataProvider
        from market_maker.external_data.base import ExternalDataProvider, MarketData
        
        # Check that TruexDataProvider inherits from ExternalDataProvider
        assert issubclass(TruexDataProvider, ExternalDataProvider), "TruexDataProvider should inherit from ExternalDataProvider"
        print("✅ TruexDataProvider correctly inherits from ExternalDataProvider")
        
        # Check that required methods exist
        provider = TruexDataProvider()
        required_methods = ['connect', 'disconnect', 'subscribe', 'unsubscribe', 'get_current_data']
        
        for method_name in required_methods:
            assert hasattr(provider, method_name), f"Missing method: {method_name}"
            method = getattr(provider, method_name)
            assert callable(method), f"Method {method_name} is not callable"
        
        print("✅ All required ExternalDataProvider methods present")
        
        # Check backward compatibility methods
        backward_compat_methods = [
            'get_client', 'get_instrument', 'get_balance', 'get_open_orders',
            'place_order', 'create_orders', 'amend_order', 'amend_orders', 'cancel_order',
            'get_position', 'get_ticker', 'is_market_open', 'get_instrument_id'
        ]
        
        for method_name in backward_compat_methods:
            assert hasattr(provider, method_name), f"Missing backward compatibility method: {method_name}"
            method = getattr(provider, method_name)
            assert callable(method), f"Backward compatibility method {method_name} is not callable"
        
        print("✅ All backward compatibility methods present")
        
    except Exception as e:
        print(f"❌ Interface structure test failed: {e}")
        return False
    
    return True

def test_truex_adapter():
    """Test that the TrueX adapter works."""
    print("🔍 Testing TrueX adapter...")
    
    try:
        from market_maker.local_data.truex import TrueX
        
        # Check that required methods exist
        required_methods = [
            'Instrument', 'InstrumentData', 'BaseBalance', 'QuoteBalance',
            'Position', 'OpenOrders', 'CreateOrders', 'PlaceOrder',
            'AmendOrders', 'AmendOrder', 'CancelOrder', 'Market',
            'Ticker', 'IsMarketOpen', 'Client', 'SetApiClient', 'Exit'
        ]
        
        # We can't instantiate TrueX without credentials, but we can check the class
        for method_name in required_methods:
            assert hasattr(TrueX, method_name), f"Missing TrueX method: {method_name}"
            method = getattr(TrueX, method_name)
            assert callable(method), f"TrueX method {method_name} is not callable"
        
        print("✅ All TrueX adapter methods present")
        
    except Exception as e:
        print(f"❌ TrueX adapter test failed: {e}")
        return False
    
    return True

def test_market_data_structure():
    """Test MarketData structure."""
    print("🔍 Testing MarketData structure...")
    
    try:
        from market_maker.external_data.base import MarketData
        
        # Create a sample MarketData instance
        data = MarketData(
            symbol="BTC-PYUSD",
            bid=50000.0,
            ask=50010.0,
            last=50005.0,
            volume=100.0,
            timestamp=1234567890.0,
            source="truex_local"
        )
        
        # Test properties
        assert data.mid == 50005.0, f"Expected mid=50005.0, got {data.mid}"
        assert data.spread == 10.0, f"Expected spread=10.0, got {data.spread}"
        assert abs(data.spread_pct - 0.02) < 0.001, f"Expected spread_pct≈0.02, got {data.spread_pct}"
        
        print("✅ MarketData structure and properties work correctly")
        
    except Exception as e:
        print(f"❌ MarketData structure test failed: {e}")
        return False
    
    return True

if __name__ == "__main__":
    print("🚀 Testing TrueX Local Data Provider Interface")
    print("=" * 50)
    
    all_tests_passed = True
    
    # Run tests
    tests = [
        test_interface_structure,
        test_truex_adapter, 
        test_market_data_structure
    ]
    
    for test in tests:
        if not test():
            all_tests_passed = False
        print()
    
    # Summary
    print("=" * 50)
    if all_tests_passed:
        print("🎉 All interface tests PASSED!")
        print("✅ Local data refactoring is working correctly")
        print("✅ Backward compatibility is maintained")
        print("✅ External data interface is implemented")
    else:
        print("❌ Some tests FAILED!")
        sys.exit(1)
