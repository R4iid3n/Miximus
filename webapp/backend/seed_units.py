"""
Seed mixer pools with pre-deposited units (PoolUnit records).

This script deposits N units into each mixer contract on-chain and stores
the resulting secrets and leaf indices in the PoolUnit table. These seeded
units become the starting anonymity set — users withdraw from them, making
their deposits and withdrawals unlinkable.

Usage (from webapp/backend/ with venv activated):
    # Set your hot wallet private key:
    export SERVICE_WALLET_PRIVATE_KEY=0x...

    # Seed all pools with 5 units each (default):
    python seed_units.py

    # Seed a specific pool with 10 units:
    python seed_units.py --symbol ETH --chain ethereum --units 10

    # Dry run (generate secrets but don't send transactions):
    python seed_units.py --dry-run

    # Seed Tron pools only:
    python seed_units.py --chain tron --units 3
"""

import argparse
import os
import sys
import time
import logging

# Ensure project root is on the path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'python'))

from app import create_app
from models import db, PoolConfig, PoolUnit
from mixer_service import get_mixer
from wallet_service import MultiChainWallet, get_chain_type

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def get_rpc_url(chain: str, network_mode: str) -> str:
    """Fetch the RPC URL for a chain from the asset registry."""
    mixer = get_mixer(network_mode)
    chain_config = mixer.registry.chains.get(chain, {})
    return chain_config.get('rpc_url', '')


def is_native_asset(symbol: str, chain: str, network_mode: str) -> bool:
    """Check if an asset is native (ETH) vs ERC20 (USDT/USDC)."""
    mixer = get_mixer(network_mode)
    asset = mixer.registry.get_asset(symbol, chain)
    if asset:
        return asset.asset_type == 'native'
    return True


def get_token_address(symbol: str, chain: str, network_mode: str) -> str | None:
    """Get the ERC20 token contract address (None for native assets)."""
    mixer = get_mixer(network_mode)
    asset = mixer.registry.get_asset(symbol, chain)
    if asset and hasattr(asset, 'contract_address'):
        return asset.contract_address
    return None


def seed_pool(pool: PoolConfig, num_units: int, wallet: MultiChainWallet,
              dry_run: bool = False) -> int:
    """Deposit `num_units` units into a single pool and record PoolUnit rows.

    Returns the number of successfully seeded units.
    """
    chain_type = get_chain_type(pool.chain)

    # Bitcoin is custodial — no contract deposits needed
    if chain_type == 'utxo' or pool.mixer_contract == 'custodial':
        logger.info(
            f"  [{pool.symbol}/{pool.chain}] Custodial pool — no on-chain seeding needed."
        )
        return 0

    mixer = get_mixer(pool.network_mode)
    rpc_url = get_rpc_url(pool.chain, pool.network_mode)
    native = is_native_asset(pool.symbol, pool.chain, pool.network_mode)
    token_address = get_token_address(pool.symbol, pool.chain, pool.network_mode)
    denomination_int = int(pool.denomination)

    existing_count = PoolUnit.query.filter_by(
        symbol=pool.symbol,
        chain=pool.chain,
        network_mode=pool.network_mode,
    ).count()

    logger.info(
        f"  [{pool.symbol}/{pool.chain}/{pool.network_mode}] "
        f"Existing units: {existing_count}, seeding {num_units} more..."
    )

    seeded = 0
    for i in range(num_units):
        # Generate a fresh secret and leaf hash
        secret = mixer.generate_secret()
        leaf_hash = mixer.compute_leaf_hash(secret)

        logger.info(
            f"    Unit {i + 1}/{num_units}: secret={secret}, leaf_hash={leaf_hash}"
        )

        if dry_run:
            logger.info(f"    [DRY RUN] Skipping on-chain deposit.")
            seeded += 1
            continue

        # Deposit the leaf into the mixer contract on-chain
        deposit_result = wallet.deposit_to_mixer(
            chain=pool.chain,
            rpc_url=rpc_url,
            contract_address=pool.mixer_contract,
            leaf_hash=leaf_hash,
            denomination=denomination_int,
            is_native=native,
            token_address=token_address,
            network_mode=pool.network_mode,
        )

        if not deposit_result.get('success'):
            logger.error(
                f"    Unit {i + 1}/{num_units}: DEPOSIT FAILED — "
                f"{deposit_result.get('error', 'unknown error')}"
            )
            # Stop seeding this pool on failure (likely out of funds)
            break

        # Record in the database
        pool_unit = PoolUnit(
            symbol=pool.symbol,
            chain=pool.chain,
            network_mode=pool.network_mode,
            secret=str(secret),
            leaf_hash=str(leaf_hash),
            leaf_index=deposit_result['leaf_index'],
            mixer_contract=pool.mixer_contract,
            deposit_tx_hash=deposit_result['tx_hash'],
            status='available',
            source='seed',
        )
        db.session.add(pool_unit)
        db.session.commit()

        seeded += 1
        logger.info(
            f"    Unit {i + 1}/{num_units}: OK — "
            f"tx={deposit_result['tx_hash']}, leaf_index={deposit_result['leaf_index']}"
        )

        # Brief pause between deposits to avoid nonce issues
        if i < num_units - 1:
            time.sleep(2)

    return seeded


def main():
    parser = argparse.ArgumentParser(
        description='Seed mixer pools with pre-deposited units for the anonymity set.'
    )
    parser.add_argument('--symbol', type=str, default=None,
                        help='Only seed pool with this symbol (e.g. ETH, USDT)')
    parser.add_argument('--chain', type=str, default=None,
                        help='Only seed pools on this chain (e.g. ethereum, tron)')
    parser.add_argument('--units', type=int, default=5,
                        help='Number of units to deposit per pool (default: 5)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Generate secrets but skip on-chain transactions')
    parser.add_argument('--network-mode', type=str, default='testnet',
                        choices=['testnet', 'mainnet'],
                        help='Network mode (default: testnet)')

    args = parser.parse_args()

    app = create_app()

    with app.app_context():
        # Build filters
        query = PoolConfig.query.filter_by(
            enabled=True,
            network_mode=args.network_mode,
        )
        if args.symbol:
            query = query.filter_by(symbol=args.symbol.upper())
        if args.chain:
            query = query.filter_by(chain=args.chain.lower())

        pools = query.all()

        if not pools:
            logger.error("No matching pools found. Run seed_pools.py first.")
            return

        # Initialize wallet
        private_key = os.environ.get('SERVICE_WALLET_PRIVATE_KEY', '')
        if not private_key and not args.dry_run:
            logger.error(
                "SERVICE_WALLET_PRIVATE_KEY not set. "
                "Set it or use --dry-run to test without transactions."
            )
            return

        wallet = None
        if not args.dry_run:
            rpc_urls = {}
            for mode in ('mainnet', 'testnet'):
                try:
                    mixer = get_mixer(mode)
                    for chain_id, chain_config in mixer.registry.chains.items():
                        url = chain_config.get('rpc_url', '')
                        if url:
                            rpc_urls[chain_id] = url
                except Exception:
                    pass

            wallet = MultiChainWallet(private_key, rpc_urls)
            logger.info(f"Wallet initialized: EVM={wallet.get_evm_address()}")

        logger.info(
            f"\nSeeding {len(pools)} pool(s) with {args.units} unit(s) each "
            f"({'DRY RUN' if args.dry_run else 'LIVE'})\n"
        )

        total_seeded = 0
        for pool in pools:
            logger.info(
                f"Pool: {pool.symbol} on {pool.chain} ({pool.network_mode}) — "
                f"contract={pool.mixer_contract}"
            )
            count = seed_pool(pool, args.units, wallet, dry_run=args.dry_run)
            total_seeded += count
            logger.info(f"  -> Seeded {count} unit(s)\n")

        # Summary
        logger.info(f"{'='*60}")
        logger.info(f"Total units seeded: {total_seeded}")
        logger.info(f"{'='*60}\n")

        # Show pool unit counts
        logger.info("Current pool unit counts:")
        for pool in pools:
            available = PoolUnit.query.filter_by(
                symbol=pool.symbol, chain=pool.chain,
                network_mode=pool.network_mode, status='available',
            ).count()
            reserved = PoolUnit.query.filter_by(
                symbol=pool.symbol, chain=pool.chain,
                network_mode=pool.network_mode, status='reserved',
            ).count()
            withdrawn = PoolUnit.query.filter_by(
                symbol=pool.symbol, chain=pool.chain,
                network_mode=pool.network_mode, status='withdrawn',
            ).count()
            logger.info(
                f"  {pool.symbol:6s} {pool.chain:12s} — "
                f"available={available}, reserved={reserved}, withdrawn={withdrawn}"
            )


if __name__ == '__main__':
    main()
