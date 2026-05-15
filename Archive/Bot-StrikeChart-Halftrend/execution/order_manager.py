"""
Async Order Manager - Non-blocking order placement and management.

Handles order execution with retries, timeouts, and LIMIT->MARKET fallback logic.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime
import functools
from enum import Enum


class OrderType(Enum):
    """Order types"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(Enum):
    """Order status"""
    PENDING = "PENDING"
    PLACED = "PLACED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class OrderResult:
    """Result from order placement"""
    success: bool
    order_id: Optional[str] = None
    filled_price: Optional[float] = None
    quantity: int = 0
    message: str = ""
    order_type: OrderType = OrderType.MARKET
    status: OrderStatus = OrderStatus.PENDING


class OrderManager:
    """
    Async order manager for trade execution.
    
    Features:
    - Async order placement (non-blocking)
    - Automatic retries on failure
    - LIMIT order with timeout -> MARKET fallback
    - Order status polling
    
    Example:
        mgr = OrderManager(api_client, config)
        
        # Place order
        result = await mgr.place_order(
            symbol="NIFTY24JAN25500CE",
            action="BUY",
            quantity=25,
            order_type="LIMIT",
            limit_price=200.0
        )
        
        if result.success:
            print(f"Order filled at {result.filled_price}")
    """
    
    def __init__(self, api_client, config: dict):
        """
        Initialize order manager.
        
        Args:
            api_client: OpenAlgo API client
            config: Configuration dict with retry/timeout settings
        """
        self.client = api_client
        self.config = config
        
        # Order execution settings
        self.max_retries = config.get("max_order_retries", 3)
        self.retry_delay_ms = config.get("retry_delay_ms", 500)
        
        # LIMIT order settings
        self.limit_timeout_sec = config.get("limit_order_timeout", 5)
        self.limit_poll_interval = config.get("limit_poll_interval", 0.5)
        
        # Order tracking
        self._pending_orders: Dict[str, OrderResult] = {}
        
    def update_config(self, new_config: dict):
        """Update configuration dynamically"""
        self.config = new_config
        self.max_retries = new_config.get("max_order_retries", 3)
        self.retry_delay_ms = new_config.get("retry_delay_ms", 500)
        self.limit_timeout_sec = new_config.get("limit_order_timeout", 5)
        self.limit_poll_interval = new_config.get("limit_poll_interval", 0.5)
    
    async def place_order(
        self,
        symbol: str,
        action: str,  # "BUY" or "SELL"
        quantity: int,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
        exchange: str = "NFO",
        product: str = "MIS"
    ) -> OrderResult:
        """
        Place an order with retries and fallback logic.
        
        Args:
            symbol: Symbol to trade
            action: "BUY" or "SELL"
            quantity: Lot size
            order_type: "MARKET" or "LIMIT"
            limit_price: Price for LIMIT orders
            exchange: Exchange (NFO, NSE, etc.)
            product: Product type (MIS, NRML, etc.)
            
        Returns:
            OrderResult with execution details
        """
        order_type_enum = OrderType.MARKET if order_type == "MARKET" else OrderType.LIMIT
        
        # LIMIT order with timeout & polling (both LIMIT and SMART_LIMIT)
        if order_type_enum == OrderType.LIMIT or order_type == "SMART_LIMIT":
            result = await self._place_limit_with_fallback(
                symbol, action, quantity, limit_price, exchange, product
            )
        else:
            result = await self._place_market(
                symbol, action, quantity, exchange, product
            )
        
        return result
    
    async def _place_market(
        self,
        symbol: str,
        action: str,
        quantity: int,
        exchange: str,
        product: str
    ) -> OrderResult:
        """
        Place MARKET order with retries.
        
        Returns:
            OrderResult
        """
        for attempt in range(self.max_retries):
            try:
                # Run sync API call in executor
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    self._place_order_sync,
                    {
                        "symbol": symbol,
                        "exchange": exchange,
                        "action": action,
                        "quantity": quantity,
                        "price": 0,  # Market order
                        "trigger_price": 0,
                        "price_type": "MARKET",
                        "product": product
                        # "order_tag": "PureOptionsBot" # Removed: Not supported by broker API
                    }
                )
                
                if response and response.get("status") == "success":
                    order_id = response.get("orderid")
                    
                    return OrderResult(
                        success=True,
                        order_id=order_id,
                        quantity=quantity,
                        message="Market order placed successfully",
                        order_type=OrderType.MARKET,
                        status=OrderStatus.PLACED
                    )
                else:
                    error_msg = response.get("message", "Unknown error") if response else "No response"
                    
                    # Retry on failure
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.retry_delay_ms / 1000)
                        continue
                    
                    return OrderResult(
                        success=False,
                        message=f"Market order failed: {error_msg}",
                        order_type=OrderType.MARKET,
                        status=OrderStatus.REJECTED
                    )
            
            except Exception as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay_ms / 1000)
                    continue
                
                return OrderResult(
                    success=False,
                    message=f"Market order exception: {str(e)}",
                    order_type=OrderType.MARKET,
                    status=OrderStatus.REJECTED
                )
        
        return OrderResult(
            success=False,
            message="Market order failed after retries",
            order_type=OrderType.MARKET,
            status=OrderStatus.REJECTED
        )
    
    async def _place_limit_with_fallback(
        self,
        symbol: str,
        action: str,
        quantity: int,
        limit_price: float,
        exchange: str,
        product: str
    ) -> OrderResult:
        """
        Place LIMIT order with timeout and auto-cancel.
        
        Handles both:
        - SIMPLE LIMIT (limit_price = VWAP)
        - SMART_LIMIT (limit_price = min(Bid, VWAP, PrevClose))
        
        Strategy:
        1. Place LIMIT order at provided price.
        2. Poll order status every 0.5s.
        3. If filled within timeout (5-8s) → success.
        4. If timeout → CANCEL (strict, no market fallback).
        
        Returns:
            OrderResult
        """
        # Place LIMIT order
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self._place_order_sync,
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "action": action,
                    "quantity": quantity,
                    "price": limit_price,
                    "trigger_price": 0,
                    "product": product
                }
            )
            
            if not response or response.get("status") != "success":
                return OrderResult(
                    success=False,
                    message=f"Smart Limit placement failed: {response}",
                    order_type=OrderType.LIMIT,
                    status=OrderStatus.REJECTED
                )
            
            order_id = response.get("orderid")
            print(f"Smart Limit order placed: {order_id} @ {limit_price}")
            
            # BUG FIX #6: Use monotonic time for accurate timeout tracking
            # asyncio.get_event_loop().time() can drift under heavy load
            import time
            start_time = time.monotonic()
            limit_timeout = self.config.get("execution", {}).get("order_timeout_sec", 8)
            
            while (time.monotonic() - start_time) < limit_timeout:
                await asyncio.sleep(self.limit_poll_interval)
                
                # Check order status
                status = await self._get_order_status(order_id)
                
                # Robust status check (handle string or other formats)
                if not status:
                    continue
                    
                status = str(status).upper()
                if status in ["COMPLETE", "FILLED"]: # 'COMPLETE' is often used by OpenAlgo
                    print(f"Smart Limit filed! ID: {order_id}")
                    return OrderResult(
                        success=True,
                        order_id=order_id,
                        filled_price=limit_price,
                        quantity=quantity,
                        message="Smart Limit filled",
                        order_type=OrderType.LIMIT,
                        status=OrderStatus.FILLED
                    )
                elif status in ["REJECTED", "CANCELLED"]:
                    print(f"Smart Limit rejected/cancelled.")
                    return OrderResult(
                        success=False,
                        message=f"Order {status}",
                        order_type=OrderType.LIMIT,
                        status=OrderStatus.REJECTED
                    )
            
            # Timeout -> Cancel LIMIT (STRICT)
            print(f"Smart Limit timeout ({limit_timeout}s). Cancelling order... (No Chase)")
            await self._cancel_order(order_id)
            
            return OrderResult(
                success=False,
                message="Smart Limit timed out (Strict Mode)",
                order_type=OrderType.LIMIT,
                status=OrderStatus.CANCELLED
            )
        
        except Exception as e:
            print(f"Smart Limit exception: {e}")
            return OrderResult(
                success=False,
                message=f"Exception: {e}",
                order_type=OrderType.LIMIT,
                status=OrderStatus.REJECTED
            )
    
    async def _get_order_status(self, order_id: str) -> str:
        """Get order status (async)"""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self.client.orderbook
            )
            
            if response:
                # Handle both dict and string responses from common API client issues
                if isinstance(response, str):
                    print(f"[DEBUG] API returned string for orderbook: {response}")
                    return "UNKNOWN"
                
                # Defensive check: ensure response is dict before calling .get()
                if not isinstance(response, dict):
                    print(f"[DEBUG] Orderbook response is not dict: {type(response)}")
                    return "UNKNOWN"
                    
                if 'data' in response:
                    for order in response['data']:
                        # Skip if order item is not a dict (API sometimes returns strings)
                        if not isinstance(order, dict):
                            continue
                        if str(order.get('orderid')) == str(order_id):
                            return order.get('status', 'PENDING')
            
        except Exception as e:
            print(f"Error getting order status: {e}")
        
        return "UNKNOWN"
    
    async def _cancel_order(self, order_id: str) -> bool:
        """Cancel an order (async)"""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                functools.partial(self.client.cancelorder, order_id=order_id)
            )
            
            # Handle string response
            if isinstance(response, str):
                logger.warning(f"API returned string for cancelorder: {response}")
                return "success" in response.lower()
                
            return response and response.get("status") == "success"
        
        except Exception as e:
            print(f"Error cancelling order: {e}")
            return False
    
    def _place_order_sync(self, order_params: dict) -> Optional[dict]:
        """Synchronous order placement (called in executor)"""
        try:
            return self.client.placeorder(**order_params)
        except Exception as e:
            print(f"Order placement error: {e}")
            return None
    
    # === ORDER BOOK UTILITIES ===
    
    async def get_open_orders(self) -> list:
        """Get list of open (pending) orders"""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self.client.orderbook
            )
            
            if response and 'data' in response:
                return [
                    order for order in response['data']
                    if order.get('status') in ['PENDING', 'PLACED']
                ]
        
        except Exception as e:
            print(f"Error getting open orders: {e}")
        
        return []
    
    async def cancel_all_orders(self) -> int:
        """Cancel all pending orders"""
        open_orders = await self.get_open_orders()
        cancelled = 0
        
        for order in open_orders:
            order_id = order.get('orderid')
            if order_id:
                success = await self._cancel_order(order_id)
                if success:
                    cancelled += 1
        
        return cancelled
