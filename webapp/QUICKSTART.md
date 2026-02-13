# Miximus Webapp — Quickstart

## Prerequisites

- Python 3.10+
- Node.js 18+

## 1. Backend Setup

```bash
cd webapp/backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Run the server
python app.py
```

Backend starts at **http://localhost:5000**

## 2. Frontend Setup

```bash
cd webapp/frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

Frontend starts at **http://localhost:5173**

## 3. Seed Pool Config

Before using the mixer, you need at least one pool in the database. Use the Flask shell or a seed script:

```python
# From webapp/backend/ with venv activated:
python -c "
from app import create_app
from models import db, PoolConfig

app = create_app()
with app.app_context():
    # Example: ETH pool on Sepolia testnet
    pool = PoolConfig(
        symbol='ETH',
        chain='ethereum',
        network_mode='testnet',
        mixer_contract='0xYOUR_DEPLOYED_CONTRACT_ADDRESS',
        denomination='60000000000000000',   # 0.06 ETH
        commission_rate=0.015,              # 1.5%
        service_wallet_address='0xYOUR_SERVICE_WALLET_ADDRESS',
        enabled=True,
        min_confirmations=3,
    )
    db.session.add(pool)
    db.session.commit()
    print('Pool created!')
"
```

## 4. Use the Mixer

1. Open **http://localhost:5173**
2. Toggle **Testnet/Mainnet** in the header
3. Go to **Mix** — select a pool, enter the recipient address
4. Send the exact amount to the displayed service address from any wallet
5. Paste your TX hash and submit
6. Track progress on the **Track Order** page — the backend handles deposit, proof generation, and withdrawal automatically

## Environment Variables

Set these in the project root `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_WALLET_PRIVATE_KEY` | — | Hot wallet private key (0x-prefixed). Funds pools and pays gas. |
| `COMMISSION_RATE` | `0.015` | Fee as decimal (0.015 = 1.5%) |
| `ORDER_EXPIRY_SECONDS` | `7200` | How long users have to pay (2 hours) |
| `MIN_CONFIRMATIONS` | `3` | Block confirmations before processing payment |
| `FLASK_ENV` | `development` | `development` = SQLite, `production` = PostgreSQL |
| `DATABASE_URL` | SQLite file | PostgreSQL URL for production |
| `SEPOLIA_RPC` | public RPC | Sepolia RPC endpoint |

## Architecture

```
User (any wallet)              Flask Backend              Blockchain
      |                            |                        |
      |-- Open webapp ------------>|                        |
      |<-- Pool list + addresses --|                        |
      |                            |                        |
      |-- POST /order/create ----->|-- Create MixOrder      |
      |<-- service address + amt --|   (secret generated    |
      |                            |    server-side)        |
      |                            |                        |
      |-- Send crypto from --------------------------------->
      |   any wallet               |                        |
      |                            |                        |
      |-- POST /order/submit-tx -->|-- Store TX hash        |
      |                            |                        |
      |   (Background processor)   |                        |
      |                            |-- verify_payment() --->|
      |                            |<-- confirmed ----------|
      |                            |                        |
      |                            |-- deposit_to_mixer() ->|
      |                            |<-- leaf_index ---------|
      |                            |                        |
      |                            |-- generate_proof()     |
      |                            |   (C++ zkSNARK)       |
      |                            |                        |
      |                            |-- withdrawViaRelayer ->|
      |                            |   (minus commission)   |
      |                            |<-- tx_hash ------------|
      |                            |                        |
      |-- GET /order/<id>/status ->|                        |
      |<-- completed + tx_hash ----|                        |
      |                            |         Recipient gets |
      |                            |         payout ------->|
```

## Denominations

| Asset | Denomination | Commission (1.5%) | Payout |
|-------|-------------|-------------------|--------|
| ETH   | 0.06 ETH    | 0.0009 ETH       | 0.0591 ETH |
| BTC   | 0.002 BTC   | 0.00003 BTC      | 0.00197 BTC |
| USDT  | 1 USDT      | 0.015 USDT       | 0.985 USDT |
| USDC  | 1 USDC      | 0.015 USDC       | 0.985 USDC |

## Testnet Faucets

- **Sepolia ETH**: https://sepoliafaucet.com
- **Tron Nile TRX**: https://nileex.io/join/getJoinPage

## Troubleshooting

**"Module not found" on backend start**
→ Make sure virtual environment is activated and `pip install -r requirements.txt` completed

**"No active pools" on frontend**
→ You need to seed at least one PoolConfig in the database (see step 3)

**"Proof generation failed"**
→ The C++ prover (`libmiximus.so`) must be built. Run `make` in `ethsnarks-miximus/` first.

**CORS errors**
→ The Vite dev server proxies `/api` to Flask on port 5000. Both must be running.

**Order stuck at "payment_detected"**
→ Not enough block confirmations yet. Wait for `min_confirmations` blocks (default: 3).
