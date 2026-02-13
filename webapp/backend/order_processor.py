"""
Background worker that processes mix orders through the pipeline.

Order lifecycle (per unit, repeated N times for multi-unit orders):
    pending_payment -> payment_detected -> payment_confirmed ->
    depositing -> deposited -> proving -> withdrawing ->
    (next unit: back to payment_confirmed, or completed if last unit)

Key mixing concept:
    - DEPOSIT phase: deposits a NEW unit into the contract (replenishes the pool)
    - WITHDRAW phase: withdraws a RESERVED unit (from pre-existing pool) to the recipient
    - The deposited and withdrawn leaves are DIFFERENT → unlinkable by timing analysis

Bitcoin custodial shortcut (no contract/proof):
    pending_payment -> payment_detected -> payment_confirmed ->
    withdrawing -> completed

Also handles: failed, expired states + releasing reserved PoolUnits on expiry.
"""

import threading
import time
import hashlib
import logging
from datetime import datetime

from models import db, MixOrder, PoolConfig, PoolUnit
from mixer_service import get_mixer
from wallet_service import MultiChainWallet, get_chain_type

logger = logging.getLogger(__name__)

# BN254 scalar field order
SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617


class OrderProcessor:
    """
    Processes mix orders through their full lifecycle in a background thread.

    For each unit in an order:
    1. Deposit a FRESH unit into the mixer contract (new secret -> new leaf)
       -> the fresh unit becomes available in the pool for future users
    2. Prove + withdraw a RESERVED unit (pre-existing pool secret)
       -> the reserved unit's leaf is consumed, funds go to recipient

    This ensures deposits and withdrawals are unlinkable — different secrets,
    different leaf indices, potentially deposited at different times.
    """

    def __init__(self, app):
        self.app = app
        self._wallet = None

    def _get_wallet(self) -> MultiChainWallet:
        """Lazily initialize the MultiChainWallet from app config."""
        if self._wallet is None:
            private_key = self.app.config.get('SERVICE_WALLET_PRIVATE_KEY', '')
            if not private_key:
                raise RuntimeError("SERVICE_WALLET_PRIVATE_KEY not configured")

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

            self._wallet = MultiChainWallet(private_key, rpc_urls)
            logger.info("MultiChainWallet initialized: EVM=%s", self._wallet.get_evm_address())

        return self._wallet

    def start(self):
        """Start the background processing thread as a daemon."""
        thread = threading.Thread(target=self._run_loop, daemon=True)
        thread.start()
        logger.info("OrderProcessor background thread started")

    def _run_loop(self):
        while True:
            try:
                with self.app.app_context():
                    self.process_detected_payments()
                    self.process_confirmed_payments()
                    self.process_deposited_orders()
                    self.expire_stale_orders()
            except Exception as e:
                logger.error(f"OrderProcessor cycle error: {e}", exc_info=True)

            time.sleep(10)

    def _get_rpc_url(self, chain: str, network_mode: str) -> str:
        mixer = get_mixer(network_mode)
        chain_config = mixer.registry.chains.get(chain, {})
        return chain_config.get('rpc_url', '')

    def _is_native_asset(self, symbol: str, chain: str, network_mode: str) -> bool:
        mixer = get_mixer(network_mode)
        asset = mixer.registry.get_asset(symbol, chain)
        if asset:
            return asset.asset_type == 'native'
        return True

    def _get_token_address(self, symbol: str, chain: str, network_mode: str) -> str | None:
        mixer = get_mixer(network_mode)
        asset = mixer.registry.get_asset(symbol, chain)
        if asset and hasattr(asset, 'contract_address'):
            return asset.contract_address
        return None

    def _init_unit_data(self, order: MixOrder):
        """Initialize the unit_data JSON array if not yet populated."""
        existing = order.get_unit_data()
        if len(existing) >= order.units:
            return

        data = []
        for i in range(order.units):
            if i < len(existing):
                data.append(existing[i])
            else:
                data.append({
                    'deposited_pool_unit_id': None,
                    'withdrawn_pool_unit_id': None,
                    'deposit_tx_hash': None,
                    'withdraw_tx_hash': None,
                    'status': 'pending',
                })
        order.set_unit_data(data)

    def _update_unit(self, order: MixOrder, unit_idx: int, updates: dict):
        """Update a specific unit's data in the unit_data JSON array."""
        data = order.get_unit_data()
        if unit_idx < len(data):
            data[unit_idx].update(updates)
            order.set_unit_data(data)

    # ------------------------------------------------------------------
    # 1. Verify detected payments
    # ------------------------------------------------------------------

    def process_detected_payments(self):
        """Verify payments on-chain against total_amount."""
        orders = MixOrder.query.filter_by(status='payment_detected').all()

        for order in orders:
            try:
                pool = PoolConfig.query.filter_by(
                    symbol=order.symbol,
                    chain=order.chain,
                    network_mode=order.network_mode,
                ).first()

                if not pool:
                    order.status = 'failed'
                    order.error_message = (
                        f"No pool config for {order.symbol}/{order.chain}/{order.network_mode}"
                    )
                    db.session.commit()
                    continue

                wallet = self._get_wallet()
                rpc_url = self._get_rpc_url(order.chain, order.network_mode)
                is_native = self._is_native_asset(order.symbol, order.chain, order.network_mode)
                token_address = self._get_token_address(order.symbol, order.chain, order.network_mode)

                verification = wallet.verify_payment(
                    chain=order.chain,
                    rpc_url=rpc_url,
                    tx_hash=order.user_tx_hash,
                    expected_amount=int(order.total_amount),
                    is_native=is_native,
                    token_address=token_address,
                    network_mode=order.network_mode,
                )

                if verification.get('error'):
                    error = verification['error']
                    if 'not found' in error.lower() or 'reverted' in error.lower():
                        order.status = 'failed'
                        order.error_message = error
                        db.session.commit()
                        logger.warning(f"Order {order.id}: payment failed — {error}")
                        continue

                if (
                    verification.get('verified')
                    and verification.get('confirmations', 0) >= pool.min_confirmations
                ):
                    order.status = 'payment_confirmed'
                    order.payment_confirmed_at = datetime.utcnow()
                    db.session.commit()
                    logger.info(
                        f"Order {order.id}: payment confirmed "
                        f"({verification['confirmations']} confirmations, "
                        f"{order.units} unit(s))"
                    )

            except Exception as e:
                logger.error(f"Order {order.id}: error verifying payment — {e}", exc_info=True)
                order.status = 'failed'
                order.error_message = f"Payment verification error: {str(e)}"
                db.session.commit()

    # ------------------------------------------------------------------
    # 2. Deposit NEW units into mixer (replenish pool), or BTC shortcut
    # ------------------------------------------------------------------

    def process_confirmed_payments(self):
        """Deposit a FRESH unit into the mixer contract to replenish the pool.

        Creates a new leaf with a new secret. The resulting PoolUnit becomes
        'available' for future users. The user's withdrawal uses a different,
        pre-existing RESERVED unit.
        """
        orders = MixOrder.query.filter_by(status='payment_confirmed').all()

        for order in orders:
            try:
                chain_type = get_chain_type(order.chain)

                # BTC custodial: no contract, send total payout directly
                if chain_type == 'utxo':
                    self._process_btc_withdrawal(order)
                    continue

                self._init_unit_data(order)

                unit_idx = order.current_unit
                order.status = 'depositing'
                db.session.commit()

                logger.info(
                    f"Order {order.id}: depositing NEW unit {unit_idx + 1}/{order.units} "
                    f"(replenishing pool)"
                )

                mixer = get_mixer(order.network_mode)
                wallet = self._get_wallet()
                rpc_url = self._get_rpc_url(order.chain, order.network_mode)
                is_native = self._is_native_asset(order.symbol, order.chain, order.network_mode)
                token_address = self._get_token_address(order.symbol, order.chain, order.network_mode)

                # Generate FRESH secret and leaf hash
                secret = mixer.generate_secret()
                leaf_hash = mixer.compute_leaf_hash(secret)

                # Deposit the leaf into the mixer contract
                deposit_result = wallet.deposit_to_mixer(
                    chain=order.chain,
                    rpc_url=rpc_url,
                    contract_address=order.mixer_contract,
                    leaf_hash=leaf_hash,
                    denomination=int(order.denomination),
                    is_native=is_native,
                    token_address=token_address,
                    network_mode=order.network_mode,
                )

                if not deposit_result.get('success'):
                    order.status = 'failed'
                    order.error_message = deposit_result.get('error', 'Deposit to mixer failed')
                    db.session.commit()
                    logger.error(f"Order {order.id}: deposit failed — {order.error_message}")
                    continue

                # Create a new PoolUnit — this replenishes the pool for future users
                new_pool_unit = PoolUnit(
                    symbol=order.symbol,
                    chain=order.chain,
                    network_mode=order.network_mode,
                    secret=str(secret),
                    leaf_hash=str(leaf_hash),
                    leaf_index=deposit_result['leaf_index'],
                    mixer_contract=order.mixer_contract,
                    deposit_tx_hash=deposit_result['tx_hash'],
                    status='available',
                    source='replenish',
                    source_order_id=order.id,
                    deposited_at=datetime.utcnow(),
                )
                db.session.add(new_pool_unit)
                db.session.flush()

                # Track in order's unit_data
                self._update_unit(order, unit_idx, {
                    'deposited_pool_unit_id': new_pool_unit.id,
                    'deposit_tx_hash': deposit_result['tx_hash'],
                    'status': 'deposited',
                })

                # Update convenience fields
                order.deposit_tx_hash = deposit_result['tx_hash']
                order.status = 'deposited'
                order.deposited_at = datetime.utcnow()
                db.session.commit()

                logger.info(
                    f"Order {order.id}: unit {unit_idx + 1}/{order.units} deposited "
                    f"(new PoolUnit #{new_pool_unit.id}, leaf={deposit_result['leaf_index']})"
                )

            except Exception as e:
                logger.error(f"Order {order.id}: deposit error — {e}", exc_info=True)
                order.status = 'failed'
                order.error_message = f"Deposit error: {str(e)}"
                db.session.commit()

    def _process_btc_withdrawal(self, order: MixOrder):
        """Handle BTC custodial flow: send total payout directly."""
        try:
            order.status = 'withdrawing'
            db.session.commit()

            wallet = self._get_wallet()
            payout = int(order.payout_amount)

            withdraw_result = wallet.withdraw_via_relayer(
                chain=order.chain,
                rpc_url='',
                contract_address='custodial',
                root=0, nullifier=0, proof_points=[0]*8,
                recipient=order.recipient_address,
                relayer_fee=int(order.commission_amount),
                payout_amount=payout,
                network_mode=order.network_mode,
            )

            if not withdraw_result.get('success'):
                order.status = 'failed'
                order.error_message = withdraw_result.get('error', 'BTC withdrawal failed')
                db.session.commit()
                logger.error(f"Order {order.id}: BTC withdrawal failed — {order.error_message}")
                return

            order.withdraw_tx_hash = withdraw_result['tx_hash']
            order.completed_units = order.units
            order.status = 'completed'
            order.withdrawn_at = datetime.utcnow()
            db.session.commit()
            logger.info(
                f"Order {order.id}: BTC withdrawal completed ({order.units} units) "
                f"— tx={order.withdraw_tx_hash}"
            )

        except Exception as e:
            logger.error(f"Order {order.id}: BTC withdrawal error — {e}", exc_info=True)
            order.status = 'failed'
            order.error_message = f"BTC withdrawal error: {str(e)}"
            db.session.commit()

    # ------------------------------------------------------------------
    # 3. Prove & withdraw RESERVED units (pre-existing pool secrets)
    # ------------------------------------------------------------------

    def process_deposited_orders(self):
        """Generate zkSNARK proof and withdraw using a RESERVED pool unit.

        The reserved unit was deposited earlier (by seeding or a previous order).
        Its secret and leaf_index are used for the proof — NOT the freshly
        deposited unit. This is what provides the mixing.

        After withdrawing:
        - If more units remain: increment current_unit, go back to payment_confirmed
        - If last unit: mark order as completed
        """
        orders = MixOrder.query.filter_by(status='deposited').all()

        for order in orders:
            try:
                chain_type = get_chain_type(order.chain)

                if chain_type == 'utxo':
                    self._process_btc_withdrawal(order)
                    continue

                unit_idx = order.current_unit

                # Find the next reserved PoolUnit (first one still in 'reserved' status)
                reserved_unit = PoolUnit.query.filter_by(
                    reserved_for_order=order.id,
                    status='reserved',
                ).order_by(PoolUnit.id).first()

                if not reserved_unit:
                    order.status = 'failed'
                    order.error_message = (
                        f'No reserved pool unit for withdrawal '
                        f'(unit {unit_idx + 1}/{order.units})'
                    )
                    db.session.commit()
                    logger.error(f"Order {order.id}: {order.error_message}")
                    continue

                order.status = 'proving'
                db.session.commit()

                logger.info(
                    f"Order {order.id}: proving unit {unit_idx + 1}/{order.units} "
                    f"using reserved PoolUnit #{reserved_unit.id} "
                    f"(leaf={reserved_unit.leaf_index})"
                )

                mixer = get_mixer(order.network_mode)
                wallet = self._get_wallet()
                rpc_url = self._get_rpc_url(order.chain, order.network_mode)
                is_native = self._is_native_asset(order.symbol, order.chain, order.network_mode)

                # Get the chain adapter
                asset = mixer.registry.get_asset(order.symbol, order.chain)
                if not asset:
                    order.status = 'failed'
                    order.error_message = f"Asset {order.symbol} on {order.chain} not found"
                    db.session.commit()
                    continue

                adapter = mixer._get_adapter(asset)

                # Use the RESERVED unit's secret and leaf_index for the proof
                secret = int(reserved_unit.secret)
                leaf_index = reserved_unit.leaf_index

                # Get Merkle tree state
                root = adapter.get_root()
                path, address_bits = adapter.get_path(leaf_index)

                # Compute ext_hash
                ext_hash = self._compute_ext_hash(
                    order.mixer_contract, order.recipient_address, chain_type,
                )

                # Generate zkSNARK proof
                proof_data = mixer._generate_proof(
                    root=root,
                    secret=secret,
                    ext_hash=ext_hash,
                    address_bits=address_bits,
                    path=path,
                    leaf_index=leaf_index,
                )

                if proof_data is None:
                    order.status = 'failed'
                    order.error_message = (
                        f'zkSNARK proof generation failed '
                        f'(unit {unit_idx + 1}, PoolUnit #{reserved_unit.id})'
                    )
                    db.session.commit()
                    continue

                # Advance to withdrawing
                order.status = 'withdrawing'
                db.session.commit()

                # Per-unit relayer fee
                denomination_int = int(order.denomination)
                relayer_fee = int(denomination_int * order.commission_rate)

                # Submit withdrawal
                withdraw_result = wallet.withdraw_via_relayer(
                    chain=order.chain,
                    rpc_url=rpc_url,
                    contract_address=order.mixer_contract,
                    root=proof_data.root,
                    nullifier=proof_data.nullifier,
                    proof_points=list(proof_data.proof),
                    recipient=order.recipient_address,
                    relayer_fee=relayer_fee,
                    is_native=is_native,
                    payout_amount=denomination_int - relayer_fee,
                    network_mode=order.network_mode,
                )

                if not withdraw_result.get('success'):
                    order.status = 'failed'
                    order.error_message = withdraw_result.get('error', 'Withdrawal failed')
                    db.session.commit()
                    logger.error(f"Order {order.id}: withdrawal failed — {order.error_message}")
                    continue

                # Mark the reserved PoolUnit as withdrawn
                reserved_unit.status = 'withdrawn'
                reserved_unit.nullifier = str(proof_data.nullifier)
                reserved_unit.withdraw_tx_hash = withdraw_result['tx_hash']
                reserved_unit.withdrawn_at = datetime.utcnow()

                # Update order tracking
                self._update_unit(order, unit_idx, {
                    'withdrawn_pool_unit_id': reserved_unit.id,
                    'withdraw_tx_hash': withdraw_result['tx_hash'],
                    'status': 'completed',
                })

                order.withdraw_tx_hash = withdraw_result['tx_hash']
                order.nullifier = str(proof_data.nullifier)
                order.completed_units = (order.completed_units or 0) + 1

                logger.info(
                    f"Order {order.id}: unit {unit_idx + 1}/{order.units} withdrawn "
                    f"(PoolUnit #{reserved_unit.id} -> {order.recipient_address[:10]}...)"
                )

                if order.completed_units >= order.units:
                    order.status = 'completed'
                    order.withdrawn_at = datetime.utcnow()
                    logger.info(f"Order {order.id}: all {order.units} units completed!")
                else:
                    order.current_unit = unit_idx + 1
                    order.status = 'payment_confirmed'
                    logger.info(
                        f"Order {order.id}: advancing to unit {unit_idx + 2}/{order.units}"
                    )

                db.session.commit()

            except Exception as e:
                logger.error(f"Order {order.id}: prove/withdraw error — {e}", exc_info=True)
                order.status = 'failed'
                order.error_message = f"Prove/withdraw error: {str(e)}"
                db.session.commit()

    @staticmethod
    def _compute_ext_hash(mixer_contract: str, recipient_address: str,
                          chain_type: str) -> int:
        """Compute ext_hash matching the contract's sha256(abi.encodePacked(...))."""
        if chain_type == 'tvm':
            try:
                from tronpy.keys import to_hex_address
                contract_hex = to_hex_address(mixer_contract)
                recipient_hex = to_hex_address(recipient_address)
                contract_bytes = bytes.fromhex(contract_hex)
                recipient_bytes = bytes.fromhex(recipient_hex)
            except ImportError:
                contract_bytes = bytes.fromhex(mixer_contract[2:].lower())
                recipient_bytes = bytes.fromhex(recipient_address[2:].lower())
        else:
            contract_bytes = bytes.fromhex(mixer_contract[2:].lower())
            recipient_bytes = bytes.fromhex(recipient_address[2:].lower())

        ext_hash = (
            int.from_bytes(
                hashlib.sha256(contract_bytes + recipient_bytes).digest(),
                'big',
            )
            % SCALAR_FIELD
        )
        return ext_hash

    # ------------------------------------------------------------------
    # 4. Expire stale orders + release reserved units
    # ------------------------------------------------------------------

    def expire_stale_orders(self):
        """Expire orders waiting for payment past their deadline.

        Also releases any reserved PoolUnits back to 'available'.
        """
        now = datetime.utcnow()
        stale_orders = MixOrder.query.filter(
            MixOrder.status == 'pending_payment',
            MixOrder.expires_at < now,
        ).all()

        for order in stale_orders:
            order.status = 'expired'

            # Release reserved pool units back to available
            reserved_units = PoolUnit.query.filter_by(
                reserved_for_order=order.id,
                status='reserved',
            ).all()

            for pu in reserved_units:
                pu.status = 'available'
                pu.reserved_for_order = None
                pu.reserved_at = None

            if reserved_units:
                logger.info(
                    f"Order {order.id}: released {len(reserved_units)} reserved unit(s) "
                    f"back to pool"
                )

            logger.info(f"Order {order.id}: expired (pending since {order.created_at})")

        if stale_orders:
            db.session.commit()
            logger.info(f"Expired {len(stale_orders)} stale order(s)")
