# Deploy on Oracle Cloud Ubuntu (24/7)

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git
sudo mkdir -p /opt/trading-bot && sudo chown ubuntu:ubuntu /opt/trading-bot
# copy the project (from your machine): rsync -av --exclude .venv --exclude data trading-bot/ ubuntu@SERVER:/opt/trading-bot/
cd /opt/trading-bot && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests          # tests use the recorded fixture, no keys
.venv/bin/python -m paperbot once --dry-run  # dry run against live public data, no keys

# secrets (never commit, never echo into shell history)
sudo install -m 600 -o ubuntu -g ubuntu /dev/null /etc/trading-bot.env
sudo nano /etc/trading-bot.env   # BINANCE_TESTNET_API_KEY=...  BINANCE_TESTNET_API_SECRET=...
set -a; . /etc/trading-bot.env; set +a; .venv/bin/python -m paperbot smoke; unset BINANCE_TESTNET_API_KEY BINANCE_TESTNET_API_SECRET

sudo cp deploy/trading-bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now trading-bot
journalctl -u trading-bot -f                 # or: tail -f logs/bot-testnet.log
```
Server clock: `timedatectl` should show NTP synchronized (Binance rejects requests >1s off).

Controls (files in `state/`):
- `touch state/PAUSE` - no new entries; open positions keep their stops/exits. `rm` to resume.
- `touch state/KILL` - market-sells every bot position, sets `halted`, process exits (systemd won't restart on exit 0). To resume: `rm state/KILL`, set `"halted": false` in `state/state-testnet.json`, `systemctl start trading-bot`.
- Daily loss cap (3% from the UTC-day start equity) pauses entries until the next UTC day automatically.

Files: `state/state-testnet.json` (positions, pending orders, last processed candle), `logs/journal-testnet.csv` (every signal/order/fill/stop/risk/reconcile event), `logs/bot-testnet.log` (rotating).
Compare with the backtest: `.venv/bin/python compare_backtest.py --mode testnet`.
