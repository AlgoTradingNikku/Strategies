# Options Trading Platform — Task List

## Milestone 1: Foundation & Configuration
- [x] config.yml — Full options configuration
- [x] app.py — FastAPI server port 8001
- [x] Folder structure creation

## Milestone 2: Option Chain & Strike Selection
- [x] data/__init__.py
- [x] data/option_chain.py — OpenAlgo chain fetcher + expiry fetcher
- [x] core/__init__.py
- [x] core/expiry_manager.py — Expiry calendar + auto-roll logic
- [x] core/strike_selector.py — ATM/ITM/OTM/PREMIUM/TREND/LIQUIDITY selectors

## Milestone 3: Three-Stage Signal Generation
- [x] core/option_signals.py — Stage 1 + Stage 3 integration
- [x] core/option_filters.py — IV proxy, OI momentum, time decay scoring
- [x] option_scanner.py — Full three-stage orchestration

## Milestone 4: Database Layer
- [x] db/__init__.py
- [x] db/option_signal_db.py
- [x] db/option_trade_db.py

## Milestone 5: Execution Engine
- [x] execution/__init__.py
- [x] execution/order_engine.py — OpenAlgo optionsorder() integration

## Milestone 6: Trade Management
- [x] execution/position_monitor.py — Premium-based monitoring

## Milestone 7: Risk Management
- [x] core/option_risk.py — Circuit breakers, daily limits, cool-down

## Milestone 8: Notifications
- [x] notifications/__init__.py
- [x] notifications/notifier.py — Telegram + WhatsApp

## Milestone 9: Frontend
- [x] frontend/index.html — Options Trading Terminal
- [x] frontend/index.css — Dark theme design system
- [x] frontend/index.js — Full terminal logic

## Milestone 10: Integration Testing
- [x] End-to-end scan flow
- [x] Config save/load
- [x] Verify port 8001 independent of Bot-Stocks
