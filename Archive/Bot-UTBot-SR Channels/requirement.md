There is a Pine Script strategy in the root folder. Please create an equivalent **Python-based Nifty Scanner Bot**.

The scanner should use **Yahoo Finance as the default data source**, but the data source must be configurable so that additional providers can be integrated in the future without requiring major code changes.

The Pine Script contains the following two strategies:

1. **UTBot**
2. **SR Channels (Support & Resistance Channels)**

The scanner should be capable of scanning configurable market segments such as **NIFTY50, NIFTY100, NIFTY200, BANKNIFTY**, and any other supported watchlist.

The scanner should identify **Buy** and **Sell** opportunities and return all stocks that satisfy the selected strategy based on the following configurable modes:

### 1. UTBot Only

* Generate Buy/Sell signals based solely on the UTBot strategy.
* The scanner should consider a UTBot Buy/Sell signal that was generated within the last **N closed candles**, where **N is configurable** (default: **2**).
* For example, if the signal was generated on the most recently closed candle or within the previous two closed candles, the stock should still qualify.

### 2. SR Channels Only

* Generate Buy/Sell signals based solely on the SR Channels strategy.
* The scanner should determine whether the current price is:

  * **Within a Support or Resistance channel**, or
  * **Near a Support or Resistance channel** based on a configurable tolerance.
* The proximity ("near") threshold should be fully configurable.

### 3. UTBot + SR Channels

* Generate signals only when **both** the UTBot and SR Channels conditions are satisfied.
* The UTBot signal and SR Channel conditions should follow the same configurable rules described above.

The Python implementation should faithfully replicate the logic of the provided Pine Script while maintaining compatibility with the configurable data source architecture.

## Configuration Requirements

The scanner should be highly configurable and should allow the user to configure:

* Data source (Yahoo Finance by default, with support for future data providers)
* Market segment (NIFTY50, NIFTY100, NIFTY200, BANKNIFTY, etc.)
* UTBot parameters
* SR Channels parameters
* Number of previous candles to consider for UTBot signals
* Support/Resistance proximity tolerance
* Enable or disable the UTBot strategy
* Enable or disable the SR Channels strategy
* Scanner timeframe and scan interval

## Telegram Alerts

The scanner should be capable of sending **Telegram alerts** whenever a stock matches the selected strategy criteria.

The following should be configurable:

* Telegram Bot Token
* Chat ID
* Alert message format

You can reference the existing Telegram implementation available in the **"BOT-UTBot-SR-LinReg-Scanner"** folder and reuse the same approach where appropriate.

## Code Quality

The implementation should be modular, well-structured, and easy to maintain. Each major component (data provider, scanner, strategies, alerting, configuration, etc.) should be separated into appropriate modules to simplify future enhancements.

The final implementation should accurately reproduce the behavior of the original Pine Script strategy while remaining extensible, configurable, and easy to integrate with additional data sources and strategies in the future.
