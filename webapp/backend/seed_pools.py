"""
Seed the database with PoolConfig entries for deployed contracts.
Run from webapp/backend/ with venv activated:
    python seed_pools.py
"""

from app import create_app
from models import db, PoolConfig

SERVICE_WALLET_EVM = "0x6B4A4F918359fBE0288Ec707d411722E21FfA0b1"
SERVICE_WALLET_TRON = "TKkWKnpHc5N5txet3wpc5RTnLjCTZEkpaZ"
# BTC address will be derived at runtime from the same private key

POOLS = [
    # ─── Sepolia Testnet (EVM) ────────────────────────────────────────
    {
        "symbol": "ETH",
        "chain": "ethereum",
        "network_mode": "testnet",
        "mixer_contract": "0x85A4ecCe24580f6d90adFFD74d9B061BD3A4f3c4",
        "denomination": "60000000000000000",  # 0.06 ETH
        "commission_rate": 0.015,
        "min_confirmations": 3,
        "service_wallet_address": SERVICE_WALLET_EVM,
    },
    {
        "symbol": "USDT",
        "chain": "ethereum",
        "network_mode": "testnet",
        "mixer_contract": "0x7a958DBd4C3BDd7ff82ed3ffab5e895a8b49C4EA",
        "denomination": "1000000",  # 1 USDT
        "commission_rate": 0.015,
        "min_confirmations": 3,
        "service_wallet_address": SERVICE_WALLET_EVM,
    },
    {
        "symbol": "USDC",
        "chain": "ethereum",
        "network_mode": "testnet",
        "mixer_contract": "0xBeB1B7eA73e18fA7E588f09C9154F2781E48578b",
        "denomination": "1000000",  # 1 USDC
        "commission_rate": 0.015,
        "min_confirmations": 3,
        "service_wallet_address": SERVICE_WALLET_EVM,
    },

    # ─── Tron Nile Testnet ────────────────────────────────────────────
    {
        "symbol": "USDT",
        "chain": "tron",
        "network_mode": "testnet",
        "mixer_contract": "TSCu8XbzpxAmdBvQjLRzzdzfckZVTRLAyr",
        "denomination": "1000000",  # 1 USDT
        "commission_rate": 0.015,
        "min_confirmations": 19,  # ~1 minute on Tron (3s blocks)
        "service_wallet_address": SERVICE_WALLET_TRON,
    },
    {
        "symbol": "USDC",
        "chain": "tron",
        "network_mode": "testnet",
        "mixer_contract": "TQwSctaE4f1WdpTc37c9ckyy322ab1AauU",
        "denomination": "1000000",  # 1 USDC
        "commission_rate": 0.015,
        "min_confirmations": 19,
        "service_wallet_address": SERVICE_WALLET_TRON,
    },

    # ─── Bitcoin Testnet (custodial — no mixer contract) ──────────────
    {
        "symbol": "BTC",
        "chain": "bitcoin",
        "network_mode": "testnet",
        "mixer_contract": "custodial",  # No smart contract — service holds BTC
        "denomination": "200000",  # 0.002 BTC (200,000 satoshis)
        "commission_rate": 0.015,
        "min_confirmations": 3,
        "service_wallet_address": "msdwEVhXXy2v3ZM8UE2SegKBoMMN692p7Y",
    },
]

def seed():
    app = create_app()
    with app.app_context():
        for pool_data in POOLS:
            existing = PoolConfig.query.filter_by(
                symbol=pool_data["symbol"],
                chain=pool_data["chain"],
                network_mode=pool_data["network_mode"],
            ).first()

            if existing:
                # Update existing
                existing.mixer_contract = pool_data["mixer_contract"]
                existing.denomination = pool_data["denomination"]
                existing.commission_rate = pool_data["commission_rate"]
                existing.service_wallet_address = pool_data["service_wallet_address"]
                existing.min_confirmations = pool_data["min_confirmations"]
                existing.enabled = True
                print(f"  Updated: {pool_data['symbol']} on {pool_data['chain']} ({pool_data['network_mode']})")
            else:
                pool = PoolConfig(
                    enabled=True,
                    **pool_data,
                )
                db.session.add(pool)
                print(f"  Created: {pool_data['symbol']} on {pool_data['chain']} ({pool_data['network_mode']})")

        db.session.commit()
        print(f"\nDone! {len(POOLS)} pool(s) seeded.")

        # Show all pools
        all_pools = PoolConfig.query.all()
        print(f"\nAll pools in database ({len(all_pools)}):")
        for p in all_pools:
            print(f"  {p.symbol:6s} {p.chain:12s} {p.network_mode:8s} {p.mixer_contract} {'ENABLED' if p.enabled else 'DISABLED'}")


if __name__ == "__main__":
    seed()
