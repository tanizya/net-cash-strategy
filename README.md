# Net Cash Select

```
Deep Value Japanese Stock Trade Strategy Dashboard
```

## Overview

RSI mean reversion strategy targeting net-cash-rich Japanese stocks.
5-stock portfolio, screened monthly from JPX-listed Prime/Standard markets.

Live dashboard: https://tanizya.github.io/net-cash-strategy/

## Strategy

```
Entry signal    RSI mean reversion on undervalued net-cash stocks
Universe        JPX Prime / Standard listed stocks
Portfolio size  5 stocks, rebalanced monthly
Stop loss       7%  when NI225 < SMA50
                10% when NI225 >= SMA50
Tax model       Japan capital gains tax @ 20.315%
```

## Stack

```
Frontend        HTML + Lightweight Charts
Data            yfinance + JPX stock list
Automation      GitHub Actions
Tagging         Claude API
Hosting         GitHub Pages
```

## License

MIT
