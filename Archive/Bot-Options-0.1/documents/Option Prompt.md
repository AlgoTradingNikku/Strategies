# Build a Production-Grade Options Trading Platform

I already have a production-quality **Stock Trading Platform** in the folder "Bot-Stocks" that includes a stock screener, strategy engine, dashboard, execution framework, and automated trade management.

I now want to build a **dedicated Options Trading Platform** within the folder "Bot-Options".

This should **not** be a copy of the Stock Trading Platform. Instead, it should be a first-class module specifically designed for options trading while sharing reusable components through a common core framework.

Your goal is **not** to simply implement my requirements.

Your goal is to design and build the **best possible production-grade Options Trading Platform**.

---

# Your Role

Act as:

* Senior Software Architect
* Quantitative Trading System Designer
* Professional Options Trader
* Product Designer
* UI/UX Designer for Professional Trading Platforms

Do not blindly follow my instructions.

Instead:

* Analyze my requirements.
* Challenge any assumptions that may lead to a better solution.
* Recommend improvements whenever appropriate.
* Explain the reasoning behind important architectural decisions.
* Think beyond implementation and optimize for maintainability, scalability, and real-world usability.

If you believe there is a better approach than what I have described, explain it and recommend it before implementation.

---

# Existing Platform

The existing Stock Trading Platform already contains:

* Stock Screener
* Dashboard
* Strategy Engine
* Signal Generation
* Indicator Framework
* Filters
* Trade Execution
* Position Management
* Risk Management
* Database
* Configuration System

Existing strategies include (but are not limited to):

* UTBOT
* SR Lines
* Other strategy modules

Existing filters include:

* EMA
* Volume
* RSI
* ADX
* Trend filters
* Momentum filters
* Volatility filters

The Options Platform should reuse common functionality wherever practical while remaining logically independent.

---

# Architecture Requirements

Build the Options Trading Platform as an independent module inside the same project.

Do **not** tightly couple it with the Stock module.

Instead, identify reusable components that should belong in a shared **Core Framework**.

Example:

Core

* Strategy Engine
* Indicators
* Filters
* Broker Layer
* Dashboard Components
* Database
* Notifications
* Utilities
* Configuration
* Risk Framework

Modules

* Stock Trading Platform
* Options Trading Platform

Both modules should depend only on the Core Framework.

If you identify a better architecture, explain why and use it.

---

# Design Freedom

You are **not** required to copy the existing Stock Trading dashboard or workflow.

Design the Options Platform as if you were building software for professional options traders.

You have complete freedom to redesign:

* Dashboard
* Navigation
* Workflows
* Layout
* Widgets
* Monitoring panels
* Trade management interface
* Analytics
* User experience

If a traditional stock screener is **not** the best interface for options trading, replace it with something better.

Possible ideas include:

* Trading Terminal
* Options Chain
* Strategy Cockpit
* Watchlists
* Live Position Monitor
* Risk Dashboard
* Analytics Workspace
* Heatmaps
* Multi-panel workspace

These are only suggestions.

Choose whichever interface you believe provides the best trading experience.

Your design should prioritize:

* Fast decision making
* Minimal clicks
* Low cognitive load
* Excellent visibility
* Efficient monitoring during live markets

---

# Strike Selection Engine

Design an intelligent Strike Selection Engine.

For configured option expiries, automatically identify the most suitable Call and Put option strikes that can be considered for buying or selling.

The strike selection engine should be modular and configurable.

Support configurable selection methods such as:

* ATM
* ITM
* OTM
* Premium Range
* Underlying Price
* Trend
* Volatility
* Liquidity
* Open Interest

Design the framework so future algorithms can easily be added, including:

* Greeks-based selection
* IV-based selection
* AI-assisted selection

---

# Signal Generation

Once strikes are shortlisted, scan them using the existing Strategy Engine.

Reuse the existing strategies wherever appropriate.

Examples include:

* UTBOT
* SR Lines
* Existing custom strategies

Support all existing filters, including:

* EMA
* Volume
* RSI
* ADX
* Trend Filters
* Momentum Filters
* Volatility Filters

Future strategies added to the platform should automatically become available to both Stock and Options modules whenever applicable.

Avoid unnecessary duplication.

---

# Dashboard Integration

Display generated option signals in a dedicated Options Dashboard.

The dashboard design is entirely your responsibility.

Design whatever interface you believe best suits professional options trading.

Each signal should display relevant information such as:

* Underlying
* Strike Price
* Expiry
* Option Type (CE/PE)
* Buy/Sell
* Strategy Name
* Confidence Score
* Current Premium
* Entry Price
* Trigger Time
* Supporting Indicators
* Filter Status
* Trade Status

Feel free to include additional information if it improves usability.

---

# Automated Trade Execution

Design an independent execution engine.

Support:

* Buy
* Sell
* Position sizing
* Quantity management
* Order validation
* Duplicate order prevention
* Retry handling
* Error recovery

Execution logic should remain independent of signal generation.

---

# Automated Trade Management

After a Buy or Sell order has been executed successfully, continuously monitor the open option position until it is closed.

Design a dedicated Trade Management Engine.

Support configurable:

### Profit Locking

Automatically lock a configurable percentage of unrealized profits after predefined profit milestones are reached.

Support multiple profit-lock levels.

### Trailing Stop Loss

Continuously trail the stop loss as the option premium moves favorably.

Support:

* Percentage-based trailing
* Premium-point trailing
* Configurable activation threshold

### Target-Based Exit

Automatically exit positions when the configured target is achieved.

Support:

* Percentage target
* Premium points
* Absolute premium value

### Stop-Loss Exit

Automatically exit positions when stop loss is hit.

Support:

* Percentage
* Premium points
* Absolute premium

### Dynamic Premium-Based Calculations

Option premiums vary significantly across strikes and expiries.

Therefore:

All calculations for:

* Stop Loss
* Target
* Profit Lock
* Trailing Stop
* Break-even
* Partial Exit

must be dynamic and based on the option's entry premium.

This ensures consistent percentage-based risk management regardless of option premium.

---

# Risk Management

Design a reusable Risk Management Engine.

Support:

* Position sizing
* Maximum capital exposure
* Maximum daily loss
* Maximum trades per day
* Maximum simultaneous positions
* Circuit breakers
* Consecutive loss protection
* Cool-down periods

If you believe additional safeguards are required, include them.

---

# Configuration

Everything should be configurable.

Avoid hardcoded values.

Configuration should include:

Strike Selection

* Expiry
* Strike Selection Method
* Premium Filters
* Liquidity Filters

Signal Generation

* Enabled Strategies
* Enabled Filters
* Timeframes
* Confirmation Rules

Execution

* Product Type
* Quantity
* Order Type
* Slippage
* Trading Session

Risk Management

* Stop Loss
* Target
* Trailing Stop
* Profit Lock
* Maximum Trades
* Capital Allocation
* Risk Limits

---

# Future Extensibility

Design the platform so future additions require minimal code changes.

Future support should include:

* Greeks
* Implied Volatility
* Volatility Surface
* Multi-leg Strategies
* Iron Condor
* Butterfly
* Calendar Spread
* Straddle
* Strangle
* Ratio Spread
* Portfolio Greeks
* AI Signal Ranking
* Machine Learning Filters

The architecture should naturally support these additions.

---

# Code Quality

Follow:

* SOLID Principles
* Clean Architecture
* Interface-driven design
* Low coupling
* High cohesion
* Modular design
* Plugin architecture where appropriate

Prefer readability and maintainability over unnecessary complexity.

---

# Development Process

Do **not** immediately begin coding.

Instead, complete the project in the following phases.

## Phase 1 – Existing Platform Analysis

* Analyze the existing Stock Trading Platform.
* Identify reusable components.
* Identify options-specific components.
* Recommend architectural improvements.

## Phase 2 – System Design

Produce:

* Folder structure
* Module architecture
* Component diagram
* Data flow
* Database changes
* Event flow
* Configuration structure

Explain every design decision.

## Phase 3 – Dashboard & User Experience

Design the complete Options Trading interface.

Show:

* Screen layouts
* Navigation
* Dashboard organization
* Widgets
* Live monitoring
* Trade management interface
* Position monitoring
* Risk visualization

Explain why your design is superior to a traditional stock screener.

## Phase 4 – Implementation Roadmap

Break the project into logical milestones.

Each milestone should be independently testable.

## Phase 5 – Implementation

Begin implementation only after the architecture and dashboard design have been finalized and approved.

Throughout development, continue recommending improvements whenever you identify opportunities to build a better trading platform.

Your objective is not merely to satisfy my requirements, but to design and build a professional-grade Options Trading Platform that would be suitable for use by serious options traders and scalable enough to evolve into a complete multi-asset trading system.

If you believe any feature, workflow, dashboard, risk control, architecture, or trading capability is missing from this specification, include it proactively. Do not restrict yourself to my requirements. Build the platform as if you were designing a commercial product for professional traders. When making such additions, explain why they improve the platform.