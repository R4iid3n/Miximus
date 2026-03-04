"""
Seed the database with PoolConfig entries for deployed contracts.
Run from webapp/backend/ with venv activated:
    python seed_pools.py
"""

from app import create_app
from models import db, PoolConfig
from pool_definitions import get_pool_definitions


def seed():
    app = create_app()
    pools_data = get_pool_definitions()

    with app.app_context():
        for pool_data in pools_data:
            existing = PoolConfig.query.filter_by(
                symbol=pool_data['symbol'],
                chain=pool_data['chain'],
                network_mode=pool_data['network_mode'],
            ).first()

            if existing:
                existing.mixer_contract          = pool_data['mixer_contract']
                existing.denomination            = pool_data['denomination']
                existing.commission_rate         = pool_data['commission_rate']
                existing.service_wallet_address  = pool_data['service_wallet_address']
                existing.min_confirmations       = pool_data['min_confirmations']
                existing.enabled                 = True
                print(f"  Updated: {pool_data['symbol']} on {pool_data['chain']} ({pool_data['network_mode']})")
            else:
                db.session.add(PoolConfig(enabled=True, **pool_data))
                print(f"  Created: {pool_data['symbol']} on {pool_data['chain']} ({pool_data['network_mode']})")

        db.session.commit()
        print(f"\nDone! {len(pools_data)} pool(s) processed.")

        all_pools = PoolConfig.query.all()
        print(f"\nAll pools in database ({len(all_pools)}):")
        for p in all_pools:
            print(f"  {p.symbol:10s} {p.chain:12s} {p.network_mode:8s} "
                  f"{p.mixer_contract[:20]}… {'ENABLED' if p.enabled else 'DISABLED'}")


if __name__ == '__main__':
    seed()
