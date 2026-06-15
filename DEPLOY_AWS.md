# Deploy the bots on AWS (Delta testnet)

Two bots, isolated by separate testnet accounts, on one EC2 box with a static
(Elastic) IP whitelisted on both Delta API keys.

| Bot | Strategy file | Symbol | TF | Service |
|-----|---------------|--------|----|---------|
| SMC | `mtf_backtester/smc_strategy_adi.py` | BTCUSD (id 84, cv 0.001, tick 0.1) | 4h | `smc-bot.service` |
| PropFirm | `mtf_backtester/PropFirmHyper-RobustRiskEngine.py` (via `propfirm_live.py`) | ETHUSD (id 1699, cv 0.01, tick 0.05) | 2h | `propfirm-bot.service` |

> Both run DRY-RUN unless `--live` (the services use `--live`, i.e. real **testnet** orders = fake money). Protective stops are real resting orders on Delta.

## 1. Two Delta testnet accounts + API keys
1. Create / use **two** Delta India **testnet** logins (separate accounts).
2. In each: API Management → create a key with **trading** permission.
3. Leave IP whitelisting until step 3 (you need the Elastic IP first).

## 2. Launch EC2 + Elastic IP
1. EC2 → launch **t3.micro**, **Ubuntu 22.04**, 8–16 GB disk. Create/download an SSH keypair.
2. **Security group:** inbound **SSH (22) from your IP only**; outbound: allow all (the bot only makes outbound HTTPS to Delta — no inbound app port needed).
3. **Elastic IP:** Allocate → Associate to the instance. This is your fixed IP.
4. In **both** Delta testnet API keys → **whitelist the Elastic IP**.

> Your home IP is dynamic — that's the whole reason for the Elastic IP. If you ever stop/rebuild the instance, re-associate the Elastic IP and it stays whitelisted.

## 3. Install on the box
```bash
ssh -i your-key.pem ubuntu@<elastic-ip>
sudo apt update && sudo apt install -y python3-venv git
# copy the repo to /home/ubuntu/bot  (scp, git, or rsync). It needs BOTH
# backtest_adi/tradingApiDeltaDemo/  AND  mtf_backtester/  (the bots import strategies from there)
cd /home/ubuntu/bot
python3 -m venv .venv
.venv/bin/pip install -r backtest_adi/tradingApiDeltaDemo/requirements.txt
.venv/bin/pip install delta-rest-client pandas numpy requests python-dotenv
```

## 4. Per-bot credentials
```bash
cd /home/ubuntu/bot/backtest_adi/tradingApiDeltaDemo
cp config.example.env .env.smc
cp config.example.env .env.propfirm
nano .env.smc        # account #1 key/secret;  DELTA_SYMBOL=BTCUSD
nano .env.propfirm   # account #2 key/secret;  DELTA_SYMBOL=ETHUSD
chmod 600 .env.smc .env.propfirm
```

## 5. Verify BEFORE going live
```bash
cd /home/ubuntu/bot/backtest_adi/tradingApiDeltaDemo
# read-only auth check per account (point test_connection at each key as needed)
/home/ubuntu/bot/.venv/bin/python test_connection.py
# dry-run one cycle each (no orders sent) — confirms candles + signal + sizing
/home/ubuntu/bot/.venv/bin/python bot_smc.py
/home/ubuntu/bot/.venv/bin/python bot_propfirm.py
```
Expect lines like `SMC BTCUSD 4h px=... | wants FLAT/LONG/SHORT | account FLAT (0)`.

## 6. Install + start the services
```bash
sudo cp smc-bot.service propfirm-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smc-bot propfirm-bot
# watch them
journalctl -u smc-bot -f
journalctl -u propfirm-bot -f
```

## 7. Operate
- Status: `systemctl status smc-bot propfirm-bot`
- Stop:   `sudo systemctl stop smc-bot` (positions/stops stay on Delta; the resting stop still protects you)
- Update code: copy new files → `sudo systemctl restart smc-bot propfirm-bot`
- Each bot re-derives its desired position from candles every cycle, so a restart self-heals (re-places the trailing stop at the correct level).

## 8. Soak, then judge
Run for weeks on testnet. Compare realized testnet results to backtest:
- SMC/BTC expectation: PF ~1.8 (validated, OOS).
- PropFirm/ETH expectation: PF ~1.8 (your ETH backtest — **not independently re-verified here**; the soak is the real test).

Only after testnet results hold up do you move to **tiny real-money size** — new real-money API keys, same Elastic IP whitelisted, flip the base URL to production, and start at minimum contracts.

## Security notes
- `.env.smc` / `.env.propfirm` are gitignored and `chmod 600`; never commit keys.
- Testnet keys only at this stage. Real-money keys are a separate, later step.
- SSH restricted to your IP; rotate keys if the box is ever shared/imaged.
