"""
Custodial mixer API — pool listing, order creation, payment submission, and status tracking.
"""

import re
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, current_app

from models import db, MixOrder, PoolConfig, PoolUnit
from config import BaseConfig

mix_bp = Blueprint('mix', __name__)

# Default decimals per symbol (used when PoolConfig does not carry a decimals field).
# Native coins on their canonical chains use these values.
SYMBOL_DECIMALS = {
    'ETH': 18,
    'BTC': 8,
    'TRX': 6,
    'BNB': 18,
    'MATIC': 18,
    'AVAX': 18,
    'FTM': 18,
    'USDT': 6,
    'USDC': 6,
    'DAI': 18,
}

DEFAULT_DECIMALS = 18


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_decimals_for_symbol(symbol: str) -> int:
    """Return the number of decimals for a given token symbol."""
    return SYMBOL_DECIMALS.get(symbol.upper(), DEFAULT_DECIMALS)


def format_amount(amount_wei_str: str, decimals: int, symbol: str) -> str:
    """Convert a wei-denominated string to a human-readable representation.

    Examples:
        format_amount("60000000000000000", 18, "ETH") -> "0.06 ETH"
        format_amount("1000000000000000000", 18, "ETH") -> "1 ETH"
        format_amount("1000000", 6, "USDT") -> "1 USDT"
    """
    if not amount_wei_str or amount_wei_str == '0':
        return f"0 {symbol}"

    d_amount = Decimal(amount_wei_str)
    divisor = Decimal(10) ** decimals

    human = d_amount / divisor

    # Strip unnecessary trailing zeros, but keep at least one digit after
    # the decimal point when the value is fractional.
    normalized = human.normalize()

    # If the result is an integer value, render without decimals.
    if normalized == normalized.to_integral_value():
        return f"{int(normalized)} {symbol}"

    # Otherwise render the full precision, stripping trailing zeros.
    return f"{normalized:f} {symbol}"


def _validate_address(address: str, chain: str) -> bool:
    """Validate a recipient address for the given chain.

    - EVM chains: 0x + 40 hex chars
    - Tron: T + 33 base58 chars (34 total)
    - Bitcoin: m/n/1/3/bc1/tb1 prefixes
    """
    if chain == 'tron':
        return bool(re.fullmatch(r'T[1-9A-HJ-NP-Za-km-z]{33}', address))
    if chain == 'bitcoin':
        return bool(re.fullmatch(
            r'([13mn][a-km-zA-HJ-NP-Z1-9]{25,34}|((bc1|tb1)[a-zA-HJ-NP-Z0-9]{25,62}))',
            address,
        ))
    # Default: EVM
    return bool(re.fullmatch(r'0x[0-9a-fA-F]{40}', address))


def _try_get_pool_balance(pool: PoolConfig) -> str | None:
    """Attempt to fetch the on-chain balance of the mixer contract.

    Returns the balance as a decimal string (in smallest unit), or None if unavailable.
    Supports EVM (web3), Tron, and Bitcoin chains.
    """
    try:
        # Bitcoin custodial — no contract balance, skip
        if pool.chain == 'bitcoin' or pool.mixer_contract == 'custodial':
            return None

        # Tron — skip for now (would need tronpy + RPC setup)
        if pool.chain == 'tron':
            return None

        # EVM chains
        from web3 import Web3

        rpc_url = current_app.config.get('RPC_URLS', {}).get(pool.chain)
        if not rpc_url:
            return None

        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            return None

        balance = w3.eth.get_balance(Web3.to_checksum_address(pool.mixer_contract))
        return str(balance)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GET /pools
# ---------------------------------------------------------------------------

@mix_bp.route('/pools', methods=['GET'])
def list_pools():
    """List all active mixing pools.

    Query params:
        network_mode  – "testnet" or "mainnet" (default "mainnet")
    """
    network_mode = request.args.get('network_mode', 'mainnet')
    if network_mode not in ('mainnet', 'testnet'):
        return jsonify({'error': 'Invalid network_mode. Must be mainnet or testnet.'}), 400

    pools = PoolConfig.query.filter_by(
        enabled=True,
        network_mode=network_mode,
    ).all()

    result = []
    for pool in pools:
        decimals = _get_decimals_for_symbol(pool.symbol)
        denomination_str = pool.denomination  # wei string

        # Commission / payout calculations (integer arithmetic on big numbers)
        denomination_int = int(denomination_str)
        commission_amount = int(denomination_int * pool.commission_rate)
        payout_int = denomination_int - commission_amount

        pool_balance = _try_get_pool_balance(pool)

        # Count available (non-reserved, non-withdrawn) units in the pool
        available_units = PoolUnit.query.filter_by(
            symbol=pool.symbol,
            chain=pool.chain,
            network_mode=network_mode,
            status='available',
        ).count()

        result.append({
            'symbol': pool.symbol,
            'chain': pool.chain,
            'denomination': denomination_str,
            'denomination_display': format_amount(denomination_str, decimals, pool.symbol),
            'commission_rate': pool.commission_rate,
            'payout_per_unit': format_amount(str(payout_int), decimals, pool.symbol),
            'payout_display': format_amount(str(payout_int), decimals, pool.symbol),
            'service_address': pool.service_wallet_address,
            'mixer_contract': pool.mixer_contract,
            'pool_balance': pool_balance,
            'available_units': available_units,
            'enabled': pool.enabled,
        })

    return jsonify({
        'pools': result,
        'network_mode': network_mode,
    })


# ---------------------------------------------------------------------------
# POST /order/create
# ---------------------------------------------------------------------------

@mix_bp.route('/order/create', methods=['POST'])
def create_order():
    """Create a new mix order.

    Body (JSON):
        symbol            – token/coin symbol (e.g. "ETH")
        chain             – chain identifier (e.g. "ethereum")
        recipient_address – where the mixed funds will be sent
        network_mode      – "mainnet" or "testnet" (default "mainnet")
        units             – number of mixer units (default 1, must be >= 1)
    """
    data = request.get_json(silent=True) or {}

    symbol = (data.get('symbol') or '').strip()
    chain = (data.get('chain') or '').strip()
    recipient_address = (data.get('recipient_address') or '').strip()
    network_mode = (data.get('network_mode') or 'mainnet').strip()

    # Units — number of denomination-sized mixer deposits
    try:
        units = int(data.get('units', 1))
    except (ValueError, TypeError):
        return jsonify({'error': 'units must be an integer'}), 400

    # --- Validation --------------------------------------------------------

    if not symbol or not chain or not recipient_address:
        return jsonify({'error': 'symbol, chain, and recipient_address are required'}), 400

    if network_mode not in ('mainnet', 'testnet'):
        return jsonify({'error': 'Invalid network_mode. Must be mainnet or testnet.'}), 400

    if units < 1:
        return jsonify({'error': 'units must be at least 1'}), 400

    if units > 100:
        return jsonify({'error': 'Maximum 100 units per order'}), 400

    # Address validation (chain-specific)
    if not _validate_address(recipient_address, chain):
        return jsonify({'error': f'Invalid recipient_address for {chain}.'}), 400

    # --- Pool lookup -------------------------------------------------------

    pool = PoolConfig.query.filter_by(
        symbol=symbol,
        chain=chain,
        network_mode=network_mode,
        enabled=True,
    ).first()

    if not pool:
        return jsonify({'error': f'No active pool found for {symbol} on {chain} ({network_mode})'}), 404

    # --- Amount calculations -----------------------------------------------

    decimals = _get_decimals_for_symbol(pool.symbol)
    denomination_int = int(pool.denomination)

    # Per-unit commission and payout
    commission_per_unit = int(denomination_int * pool.commission_rate)
    payout_per_unit = denomination_int - commission_per_unit

    # Totals across all units
    total_amount = denomination_int * units
    total_commission = commission_per_unit * units
    total_payout = payout_per_unit * units

    # --- Reserve pool units (non-BTC only) ---------------------------------

    is_custodial = (pool.mixer_contract == 'custodial' or chain == 'bitcoin')

    if not is_custodial:
        available = PoolUnit.query.filter_by(
            symbol=symbol,
            chain=chain,
            network_mode=network_mode,
            status='available',
        ).limit(units).all()

        if len(available) < units:
            return jsonify({
                'error': (
                    f'Недостаточно ликвидности в пуле. '
                    f'Доступно: {len(available)}, запрошено: {units}'
                ),
            }), 409

    # --- Create order ------------------------------------------------------

    expiry_seconds = current_app.config.get(
        'ORDER_EXPIRY_SECONDS', BaseConfig.ORDER_EXPIRY_SECONDS
    )

    order = MixOrder(
        symbol=symbol,
        chain=chain,
        network_mode=network_mode,
        recipient_address=recipient_address,
        service_address=pool.service_wallet_address,
        denomination=pool.denomination,
        units=units,
        total_amount=str(total_amount),
        commission_rate=pool.commission_rate,
        commission_amount=str(total_commission),
        payout_amount=str(total_payout),
        mixer_contract=pool.mixer_contract,
        status='pending_payment',
        expires_at=datetime.utcnow() + timedelta(seconds=expiry_seconds),
    )

    db.session.add(order)
    db.session.flush()  # get order.id before reserving

    # Reserve the pool units for this order
    if not is_custodial:
        now = datetime.utcnow()
        for pu in available:
            pu.status = 'reserved'
            pu.reserved_for_order = order.id
            pu.reserved_at = now

    db.session.commit()

    return jsonify({
        'order_id': order.id,
        'service_address': order.service_address,
        'denomination': order.denomination,
        'denomination_display': format_amount(order.denomination, decimals, symbol),
        'units': units,
        'total_amount': str(total_amount),
        'total_amount_display': format_amount(str(total_amount), decimals, symbol),
        'commission_rate': order.commission_rate,
        'payout_display': format_amount(str(total_payout), decimals, symbol),
        'expires_at': order.expires_at.isoformat(),
        'status': order.status,
    }), 201


# ---------------------------------------------------------------------------
# POST /order/submit-tx
# ---------------------------------------------------------------------------

@mix_bp.route('/order/submit-tx', methods=['POST'])
def submit_tx():
    """Submit a payment transaction hash for an existing order.

    Body (JSON):
        order_id – UUID of the order
        tx_hash  – on-chain transaction hash
    """
    data = request.get_json(silent=True) or {}

    order_id = (data.get('order_id') or '').strip()
    tx_hash = (data.get('tx_hash') or '').strip()

    if not order_id or not tx_hash:
        return jsonify({'error': 'order_id and tx_hash are required'}), 400

    order = MixOrder.query.get(order_id)
    if not order:
        return jsonify({'error': 'Order not found'}), 404

    # Must still be awaiting payment
    if order.status != 'pending_payment':
        return jsonify({
            'error': f'Order is not awaiting payment. Current status: {order.status}',
        }), 409

    # Must not be expired
    if datetime.utcnow() > order.expires_at:
        order.status = 'expired'
        db.session.commit()
        return jsonify({'error': 'Order has expired'}), 410

    order.user_tx_hash = tx_hash
    order.status = 'payment_detected'
    order.payment_detected_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        'order_id': order.id,
        'status': order.status,
        'message': 'Payment transaction recorded. The service will confirm and process your mix.',
    })


# ---------------------------------------------------------------------------
# GET /order/<order_id>/status
# ---------------------------------------------------------------------------

@mix_bp.route('/order/<order_id>/status', methods=['GET'])
def order_status(order_id: str):
    """Return the current status and progress steps for an order."""
    order = MixOrder.query.get(order_id)
    if not order:
        return jsonify({'error': 'Order not found'}), 404

    decimals = _get_decimals_for_symbol(order.symbol)
    result = order.to_dict()
    result['steps'] = order.get_steps()
    result['denomination_display'] = format_amount(order.denomination, decimals, order.symbol)
    result['total_amount_display'] = format_amount(order.total_amount, decimals, order.symbol)
    result['payout_display'] = format_amount(
        order.payout_amount or '0', decimals, order.symbol
    )

    return jsonify(result)
