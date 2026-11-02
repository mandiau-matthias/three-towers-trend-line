# Trend Line
![A worked example showing a weekly dip and recovery](examples/warning.svg)

[Open the editable Pine indicator](trend_line.pine) · [Backtest strategy](trend_line_backtest.pine) · [Python reference model](research/model.py)

I built this indicator to follow an established weekly trend. Gold means the model holds. Grey means it waits in cash. Open a standard candlestick chart in TradingView, copy the entire indicator into a new Pine Editor script and choose **Add to chart**. Use a 1W chart for the strategy.

### A warning that never became a sell

In the synthetic example above, the observation confirmed on 11 December 2023 closed at 1,492.71 and triggered a warning. The model still held. The next weekly observation closed at 1,959.18, the warning cleared, and there was no sell. The example uses invented prices and the same Python model as the tests. Run it yourself:

```sh
python3 scripts/warning_example.py
python3 -m unittest discover -s tests
```

### How a signal reaches the chart

The model waits for the weekly close. A sell needs confirmation across two qualifying closes. Intraday movement and the distance to the line are context. Match the feed, timeframe, chart type, and loaded history when comparing results. The strategy defaults to 10,000 initial capital, 99% exposure, 0.3% commission per side and zero strategy slippage. Set realistic slippage ticks in Strategy Properties. The on-chart trade calculator has its own percentage slippage assumption.

### Rebuild and contribute

Edit the shared files in `pine/`, run `python3 scripts/build_pine.py`, and commit both generated Pine files. A good first contribution is a synthetic sequence that exposes a case where the Pine and Python outputs disagree. Include the feed and observation time when reporting a chart discrepancy.

The source is candidate **1.6.6** and uses the weekly 1.5.0 model. On 2 November 2026, TradingView’s Pine Editor compiled both the indicator and the strategy from source commit `42d9e8b` on a weekly BITSTAMP:BTCUSD chart. The account’s chart limit prevented a visual comparison of this candidate with the reference signals. That check is still required for the Community Script release. The Python suite covers 24 reference and backtest cases. MIT covers the original code. Supply your own instrument history if extending the example to real data.
