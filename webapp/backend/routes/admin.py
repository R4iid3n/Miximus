"""
Admin API routes — protected by JWT bearer token.

Endpoints:
    POST /api/admin/login               — authenticate, get 24h JWT
    GET  /api/admin/stats               — order counts + pool health summary
    GET  /api/admin/orders              — list orders (filtered, paginated)
    GET  /api/admin/pools               — pool configs + live unit counts
    POST /api/admin/seed                — seed units for a pool (background job)
    GET  /api/admin/seed-status/<id>    — poll seeding progress
    GET  /api/admin/balances            — service wallet balances (EVM + BTC)
"""

import re
import uuid
import logging
import threading
import datetime
import time
import os
from functools import wraps

import jwt
import requests as http_requests

from flask import Blueprint, request, jsonify, current_app
from models import db, MixOrder, PoolConfig, PoolUnit

# .env lives three directories above this file
# routes/ → backend/ → webapp/ → project root
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..', '..'))
_ENV_PATH = os.path.join(_PROJECT_ROOT, '.env')

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)

# In-memory job tracker: job_id -> {total, done, failed, running, errors}
_seed_jobs: dict = {}


# ──────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_token(secret_key: str) -> str:
    payload = {
        'sub': 'admin',
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24),
    }
    return jwt.encode(payload, secret_key, algorithm='HS256')


def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        token = auth.removeprefix('Bearer ').strip()
        if not token:
            return jsonify({'error': 'Missing token'}), 401
        try:
            jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


# ──────────────────────────────────────────────────────────────────────────────
# Login
# ──────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json(force=True) or {}
    username = data.get('username', '')
    password = data.get('password', '')

    cfg_user = current_app.config.get('ADMIN_USERNAME', '')
    cfg_pass = current_app.config.get('ADMIN_PASSWORD', '')

    if not cfg_pass or username != cfg_user or password != cfg_pass:
        return jsonify({'error': 'Invalid credentials'}), 401

    token = _make_token(current_app.config['SECRET_KEY'])
    return jsonify({'token': token})


# ──────────────────────────────────────────────────────────────────────────────
# Stats
# ──────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/stats', methods=['GET'])
@require_admin
def admin_stats():
    all_orders = MixOrder.query.all()
    by_status: dict = {}
    for order in all_orders:
        by_status[order.status] = by_status.get(order.status, 0) + 1

    pools = PoolConfig.query.filter_by(enabled=True).all()
    pool_summary = []
    for pool in pools:
        available = PoolUnit.query.filter_by(
            symbol=pool.symbol, chain=pool.chain,
            network_mode=pool.network_mode, status='available').count()
        reserved = PoolUnit.query.filter_by(
            symbol=pool.symbol, chain=pool.chain,
            network_mode=pool.network_mode, status='reserved').count()
        withdrawn = PoolUnit.query.filter_by(
            symbol=pool.symbol, chain=pool.chain,
            network_mode=pool.network_mode, status='withdrawn').count()
        pool_summary.append({
            'symbol': pool.symbol,
            'chain': pool.chain,
            'network_mode': pool.network_mode,
            'available': available,
            'reserved': reserved,
            'withdrawn': withdrawn,
        })

    return jsonify({
        'orders_by_status': by_status,
        'total_orders': len(all_orders),
        'pools': pool_summary,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Orders
# ──────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/orders', methods=['GET'])
@require_admin
def admin_orders():
    status = request.args.get('status')
    symbol = request.args.get('symbol')
    chain = request.args.get('chain')
    network_mode = request.args.get('network_mode')
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    q = MixOrder.query
    if status:
        q = q.filter(MixOrder.status == status)
    if symbol:
        q = q.filter(MixOrder.symbol == symbol.upper())
    if chain:
        q = q.filter(MixOrder.chain == chain.lower())
    if network_mode:
        q = q.filter(MixOrder.network_mode == network_mode)

    total = q.count()
    orders = q.order_by(MixOrder.created_at.desc()).offset(offset).limit(limit).all()

    return jsonify({
        'orders': [o.to_dict() for o in orders],
        'total': total,
        'limit': limit,
        'offset': offset,
    })


# ──────────────────────────────────────────────────────────────────────────────
# Pools
# ──────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/pools', methods=['GET'])
@require_admin
def admin_pools():
    pools = PoolConfig.query.all()
    result = []
    for pool in pools:
        available = PoolUnit.query.filter_by(
            symbol=pool.symbol, chain=pool.chain,
            network_mode=pool.network_mode, status='available').count()
        reserved = PoolUnit.query.filter_by(
            symbol=pool.symbol, chain=pool.chain,
            network_mode=pool.network_mode, status='reserved').count()
        withdrawn = PoolUnit.query.filter_by(
            symbol=pool.symbol, chain=pool.chain,
            network_mode=pool.network_mode, status='withdrawn').count()
        d = pool.to_dict()
        d['id'] = pool.id
        d['available'] = available
        d['reserved'] = reserved
        d['withdrawn'] = withdrawn
        result.append(d)
    return jsonify({'pools': result})


@admin_bp.route('/admin/pools/<int:pool_id>', methods=['PATCH'])
@require_admin
def admin_update_pool(pool_id: int):
    """Update editable fields on a single pool config.

    Accepted fields: service_wallet_address, enabled.
    Only provided fields are changed (partial update).
    """
    pool = db.session.get(PoolConfig, pool_id)
    if not pool:
        return jsonify({'error': 'Pool not found'}), 404

    data = request.get_json(force=True) or {}
    changed = []

    if 'service_wallet_address' in data:
        addr = (data['service_wallet_address'] or '').strip()
        if not addr:
            return jsonify({'error': 'service_wallet_address cannot be empty'}), 400
        pool.service_wallet_address = addr
        changed.append('service_wallet_address')

    if 'enabled' in data:
        pool.enabled = bool(data['enabled'])
        changed.append('enabled')

    if not changed:
        return jsonify({'error': 'No editable fields provided'}), 400

    db.session.commit()
    logger.info(f'[admin update-pool] pool_id={pool_id} changed={changed}')
    d = pool.to_dict()
    d['id'] = pool.id
    return jsonify(d)


# ──────────────────────────────────────────────────────────────────────────────
# Seeding
# ──────────────────────────────────────────────────────────────────────────────

def _get_asset_info(symbol: str, chain: str, network_mode: str):
    """Return (is_native, token_address, rpc_url) for a pool."""
    from mixer_service import get_mixer
    mixer = get_mixer(network_mode)
    chain_config = mixer.registry.chains.get(chain, {})
    rpc_url = chain_config.get('rpc_url', '')
    asset = mixer.registry.get_asset(symbol, chain)
    if asset:
        is_native = asset.asset_type == 'native'
        token_address = getattr(asset, 'contract_address', None)
    else:
        is_native = True
        token_address = None
    return is_native, token_address, rpc_url


def _run_seed(app, pool_id: int, num_units: int, job_id: str):
    """Background thread: deposit `num_units` into the pool contract."""
    from wallet_service import MultiChainWallet
    from mixer_service import get_mixer

    _seed_jobs[job_id]['running'] = True
    try:
        with app.app_context():
            pool = db.session.get(PoolConfig, pool_id)
            if not pool:
                _seed_jobs[job_id]['errors'].append('Pool not found')
                return

            private_key = app.config['SERVICE_WALLET_PRIVATE_KEY']

            # Build RPC URL map from asset registry
            rpc_urls = {}
            for mode in ('mainnet', 'testnet'):
                try:
                    mixer = get_mixer(mode)
                    for chain_id, chain_cfg in mixer.registry.chains.items():
                        url = chain_cfg.get('rpc_url', '')
                        if url:
                            rpc_urls[chain_id] = url
                except Exception:
                    pass

            wallet = MultiChainWallet(private_key, rpc_urls)
            mixer_svc = get_mixer(pool.network_mode)
            is_native, token_address, rpc_url = _get_asset_info(
                pool.symbol, pool.chain, pool.network_mode)
            denomination_int = int(pool.denomination)

            _seed_jobs[job_id]['total'] = num_units

            for i in range(num_units):
                try:
                    secret = mixer_svc.generate_secret()
                    leaf_hash = mixer_svc.compute_leaf_hash(secret)

                    result = wallet.deposit_to_mixer(
                        chain=pool.chain,
                        rpc_url=rpc_url,
                        contract_address=pool.mixer_contract,
                        leaf_hash=leaf_hash,
                        denomination=denomination_int,
                        is_native=is_native,
                        token_address=token_address,
                        network_mode=pool.network_mode,
                    )

                    if result.get('success'):
                        unit = PoolUnit(
                            symbol=pool.symbol,
                            chain=pool.chain,
                            network_mode=pool.network_mode,
                            secret=str(secret),
                            leaf_hash=str(leaf_hash),
                            leaf_index=result['leaf_index'],
                            mixer_contract=pool.mixer_contract,
                            deposit_tx_hash=result['tx_hash'],
                            status='available',
                            source='seed',
                        )
                        db.session.add(unit)
                        db.session.commit()
                        _seed_jobs[job_id]['done'] += 1
                        logger.info(
                            f"[admin seed] {pool.symbol}/{pool.chain} unit {i+1}/{num_units} OK "
                            f"tx={result['tx_hash']}"
                        )
                    else:
                        err = result.get('error', 'unknown error')
                        _seed_jobs[job_id]['failed'] += 1
                        _seed_jobs[job_id]['errors'].append(f"Unit {i+1}: {err}")
                        logger.error(f"[admin seed] unit {i+1} failed: {err}")
                        break  # stop on failure (likely out of funds)

                    if i < num_units - 1:
                        time.sleep(2)

                except Exception as e:
                    _seed_jobs[job_id]['failed'] += 1
                    _seed_jobs[job_id]['errors'].append(f"Unit {i+1}: {e}")
                    logger.exception(f"[admin seed] unit {i+1} exception")
                    break

    except Exception as e:
        _seed_jobs[job_id]['errors'].append(str(e))
        logger.exception('[admin seed] outer exception')
    finally:
        _seed_jobs[job_id]['running'] = False


@admin_bp.route('/admin/seed', methods=['POST'])
@require_admin
def admin_seed():
    data = request.get_json(force=True) or {}
    symbol = (data.get('symbol') or '').upper()
    chain = (data.get('chain') or '').lower()
    network_mode = data.get('network_mode', 'mainnet')
    units = int(data.get('units', 5))

    if units < 1 or units > 50:
        return jsonify({'error': 'units must be 1–50'}), 400

    pool = PoolConfig.query.filter_by(
        symbol=symbol, chain=chain, network_mode=network_mode
    ).first()
    if not pool:
        return jsonify({'error': 'Pool not found'}), 404
    if pool.mixer_contract == 'custodial':
        return jsonify({'error': 'Cannot seed a custodial pool on-chain'}), 400

    job_id = str(uuid.uuid4())[:8]
    _seed_jobs[job_id] = {
        'total': units, 'done': 0, 'failed': 0, 'running': False, 'errors': []
    }

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_seed, args=(app, pool.id, units, job_id), daemon=True
    )
    thread.start()

    return jsonify({'job_id': job_id})


@admin_bp.route('/admin/seed-status/<job_id>', methods=['GET'])
@require_admin
def admin_seed_status(job_id):
    job = _seed_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify({
        'job_id': job_id,
        'total': job['total'],
        'done': job['done'],
        'failed': job['failed'],
        'running': job['running'],
        'errors': job['errors'],
    })


# ──────────────────────────────────────────────────────────────────────────────
# Wallet Balances
# ──────────────────────────────────────────────────────────────────────────────

def _get_btc_balance_str(address: str, testnet: bool = False) -> str:
    """Fetch confirmed BTC balance from Blockstream. Returns e.g. '0.00123456'."""
    try:
        base = 'https://blockstream.info/testnet/api' if testnet else 'https://blockstream.info/api'
        resp = http_requests.get(f'{base}/address/{address}', timeout=10)
        resp.raise_for_status()
        stats = resp.json().get('chain_stats', {})
        balance_sat = stats.get('funded_txo_sum', 0) - stats.get('spent_txo_sum', 0)
        return f"{balance_sat / 1e8:.8f}"
    except Exception as e:
        return f"error: {e}"


@admin_bp.route('/admin/balances', methods=['GET'])
@require_admin
def admin_balances():
    from wallet_service import MultiChainWallet
    from mixer_service import get_mixer
    from web3 import Web3

    private_key = current_app.config['SERVICE_WALLET_PRIVATE_KEY']

    rpc_urls = {}
    for mode in ('mainnet', 'testnet'):
        try:
            mixer = get_mixer(mode)
            for chain_id, chain_cfg in mixer.registry.chains.items():
                url = chain_cfg.get('rpc_url', '')
                if url:
                    rpc_urls[chain_id] = url
        except Exception:
            pass

    wallet = MultiChainWallet(private_key, rpc_urls)
    evm_address = wallet.get_evm_address()

    # EVM MATIC balance (Polygon mainnet)
    evm_balance = '?'
    try:
        polygon_rpc = rpc_urls.get('polygon', 'https://polygon-bor-rpc.publicnode.com')
        w3 = Web3(Web3.HTTPProvider(polygon_rpc))
        bal_wei = w3.eth.get_balance(evm_address)
        evm_balance = f"{bal_wei / 1e18:.6f}"
    except Exception as e:
        evm_balance = f"error: {e}"

    # BTC pool addresses & balances
    btc_main = PoolConfig.query.filter_by(
        symbol='BTC', chain='bitcoin', network_mode='mainnet').first()
    btc_test = PoolConfig.query.filter_by(
        symbol='BTC', chain='bitcoin', network_mode='testnet').first()

    btc_main_addr = btc_main.service_wallet_address if btc_main else 'N/A'
    btc_test_addr = btc_test.service_wallet_address if btc_test else 'N/A'

    return jsonify({
        'evm': {
            'address': evm_address,
            'balance_matic': evm_balance,
        },
        'btc_mainnet': {
            'address': btc_main_addr,
            'balance_btc': _get_btc_balance_str(btc_main_addr) if btc_main else 'N/A',
        },
        'btc_testnet': {
            'address': btc_test_addr,
            'balance_btc': _get_btc_balance_str(btc_test_addr, testnet=True) if btc_test else 'N/A',
        },
    })


# ──────────────────────────────────────────────────────────────────────────────
# Pool Initialisation  (runs seed_pools logic without the CLI)
# ──────────────────────────────────────────────────────────────────────────────

@admin_bp.route('/admin/init-pools', methods=['POST'])
@require_admin
def admin_init_pools():
    """Create or update PoolConfig rows from the canonical pool definitions."""
    from pool_definitions import get_pool_definitions

    private_key = current_app.config.get('SERVICE_WALLET_PRIVATE_KEY', '')
    pools_data = get_pool_definitions(private_key)

    created = updated = 0
    for pool_data in pools_data:
        existing = PoolConfig.query.filter_by(
            symbol=pool_data['symbol'],
            chain=pool_data['chain'],
            network_mode=pool_data['network_mode'],
        ).first()

        if existing:
            existing.mixer_contract         = pool_data['mixer_contract']
            existing.denomination           = pool_data['denomination']
            existing.commission_rate        = pool_data['commission_rate']
            existing.service_wallet_address = pool_data['service_wallet_address']
            existing.min_confirmations      = pool_data['min_confirmations']
            existing.enabled                = True
            updated += 1
        else:
            db.session.add(PoolConfig(enabled=True, **pool_data))
            created += 1

    db.session.commit()
    logger.info(f'[admin init-pools] created={created} updated={updated}')
    return jsonify({'created': created, 'updated': updated, 'total': len(pools_data)})


# ──────────────────────────────────────────────────────────────────────────────
# Wallet Settings  (view / change SERVICE_WALLET_PRIVATE_KEY)
# ──────────────────────────────────────────────────────────────────────────────

def _update_env_value(key: str, value: str):
    """Update or append KEY=value in the project .env file."""
    if not os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, 'a') as f:
            f.write(f'{key}={value}\n')
        return

    with open(_ENV_PATH, 'r') as f:
        lines = f.readlines()

    found = False
    new_lines = []
    for line in lines:
        if re.match(rf'^{re.escape(key)}\s*=', line):
            new_lines.append(f'{key}={value}\n')
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f'{key}={value}\n')

    with open(_ENV_PATH, 'w') as f:
        f.writelines(new_lines)


@admin_bp.route('/admin/fee-wallets', methods=['GET'])
@require_admin
def admin_get_fee_wallets():
    """Return current commission fee wallet addresses for both mainnet and testnet."""
    return jsonify({
        'mainnet': {
            'evm':  current_app.config.get('FEE_WALLET_EVM',  ''),
            'tron': current_app.config.get('FEE_WALLET_TRON', ''),
            'btc':  current_app.config.get('FEE_WALLET_BTC',  ''),
        },
        'testnet': {
            'evm':  current_app.config.get('FEE_WALLET_EVM_TESTNET',  ''),
            'tron': current_app.config.get('FEE_WALLET_TRON_TESTNET', ''),
            'btc':  current_app.config.get('FEE_WALLET_BTC_TESTNET',  ''),
        },
    })


@admin_bp.route('/admin/fee-wallets', methods=['POST'])
@require_admin
def admin_update_fee_wallets():
    """
    Update commission fee wallet addresses for mainnet and/or testnet.

    Body: {
        "mainnet": { "evm": "0x...", "tron": "T...", "btc": "1..." },
        "testnet": { "evm": "0x...", "tron": "T...", "btc": "tb1..." }
    }

    Writes to .env AND updates in-memory (no restart required).
    Empty string = keep commission in the service wallet (no forwarding).
    """
    data = request.get_json(force=True) or {}

    def _extract(net):
        sub = data.get(net) or {}
        return {
            'evm':  (sub.get('evm')  or '').strip(),
            'tron': (sub.get('tron') or '').strip(),
            'btc':  (sub.get('btc')  or '').strip(),
        }

    mn = _extract('mainnet')
    tn = _extract('testnet')

    # Write to .env
    _update_env_value('FEE_WALLET_EVM',          mn['evm'])
    _update_env_value('FEE_WALLET_TRON',         mn['tron'])
    _update_env_value('FEE_WALLET_BTC',          mn['btc'])
    _update_env_value('FEE_WALLET_EVM_TESTNET',  tn['evm'])
    _update_env_value('FEE_WALLET_TRON_TESTNET', tn['tron'])
    _update_env_value('FEE_WALLET_BTC_TESTNET',  tn['btc'])

    # Update live app config
    current_app.config['FEE_WALLET_EVM']          = mn['evm']
    current_app.config['FEE_WALLET_TRON']         = mn['tron']
    current_app.config['FEE_WALLET_BTC']          = mn['btc']
    current_app.config['FEE_WALLET_EVM_TESTNET']  = tn['evm']
    current_app.config['FEE_WALLET_TRON_TESTNET'] = tn['tron']
    current_app.config['FEE_WALLET_BTC_TESTNET']  = tn['btc']

    # Update order processor in-memory (no restart needed)
    processor = getattr(current_app, 'order_processor', None)
    if processor:
        processor._fee_wallets['mainnet'] = {'evm': mn['evm'], 'tvm': mn['tron'], 'utxo': mn['btc']}
        processor._fee_wallets['testnet'] = {'evm': tn['evm'], 'tvm': tn['tron'], 'utxo': tn['btc']}

    logger.info(f'[admin fee-wallets] mainnet={mn} testnet={tn}')

    return jsonify({
        'success': True,
        'mainnet': mn, 'testnet': tn,
        'live_updated': processor is not None,
        'note': 'Адреса комиссий обновлены. Изменения применены немедленно.',
    })


@admin_bp.route('/admin/wallet', methods=['GET'])
@require_admin
def admin_get_wallet():
    """Return derived addresses for the current service wallet (no private key exposed)."""
    from pool_definitions import derive_all_addresses, EVM_CHAINS

    private_key = current_app.config.get('SERVICE_WALLET_PRIVATE_KEY', '')
    if not private_key:
        return jsonify({'error': 'SERVICE_WALLET_PRIVATE_KEY not configured'}), 400

    addrs = derive_all_addresses(private_key)
    return jsonify(addrs)


@admin_bp.route('/admin/wallet', methods=['POST'])
@require_admin
def admin_update_wallet():
    """
    Change the service wallet private key.

    Body: { "private_key": "<hex>" }

    Actions:
      1. Validate the key by deriving addresses.
      2. Update SERVICE_WALLET_PRIVATE_KEY in .env.
      3. Update service_wallet_address in all PoolConfig rows.

    Note: the Flask process must be restarted for the new key to take effect
    in memory (wallet_service uses the key from app.config loaded at startup).
    """
    from pool_definitions import derive_all_addresses, EVM_CHAINS

    data = request.get_json(force=True) or {}
    new_key = (data.get('private_key') or '').strip()
    if not new_key:
        return jsonify({'error': 'private_key is required'}), 400

    pk_clean = new_key.lstrip('0x')
    if len(pk_clean) != 64:
        return jsonify({'error': 'private_key must be a 32-byte (64 hex char) key'}), 400

    addrs = derive_all_addresses(pk_clean)
    # Check for derivation errors
    errors = {k: v for k, v in addrs.items() if 'error' in str(v).lower()}
    if errors:
        return jsonify({'error': 'Key derivation failed', 'details': errors}), 400

    # 1. Write new key to .env
    _update_env_value('SERVICE_WALLET_PRIVATE_KEY', new_key)

    # 2. Update service_wallet_address in all PoolConfig rows
    pools_updated = 0
    for pool in PoolConfig.query.all():
        if pool.chain in EVM_CHAINS:
            new_addr = addrs['evm_address']
        elif pool.chain == 'bitcoin' and pool.network_mode == 'mainnet':
            new_addr = addrs['btc_mainnet_address']
        elif pool.chain == 'bitcoin' and pool.network_mode == 'testnet':
            new_addr = addrs['btc_testnet_address']
        elif pool.chain == 'tron':
            new_addr = addrs['tron_address']
        else:
            continue

        if pool.service_wallet_address != new_addr:
            pool.service_wallet_address = new_addr
            pools_updated += 1

    db.session.commit()
    logger.info(f'[admin wallet] key updated, pools_updated={pools_updated}')

    return jsonify({
        'success': True,
        'addresses': addrs,
        'pools_updated': pools_updated,
        'restart_required': True,
        'note': (
            'Приватный ключ обновлён в .env и адреса пулов обновлены в базе данных. '
            'Перезапустите Flask для применения нового ключа в памяти.'
        ),
    })
