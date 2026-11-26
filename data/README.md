# data/
Raw Binance spot klines (BTCUSDT, ETHUSDT; 1h and 4h; 2020-01-01 → today) are not committed (~10 MB of parquet).
Re-download (no API key needed; monthly zips from data.binance.vision + public REST for the current month):

```bash
python -m tradebot.data      # writes data/{SYMBOL}_{TF}.parquet
python run_backtests.py      # regenerates results.csv, grid_results.csv, oos_equity.png
```
Small recorded fixture used by the tests lives in `tests/fixtures/`.
