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

import json
import os
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
        self._fee_wallets = {
            'mainnet': {
                'evm':  app.config.get('FEE_WALLET_EVM',  ''),
                'tvm':  app.config.get('FEE_WALLET_TRON', ''),
                'utxo': app.config.get('FEE_WALLET_BTC',  ''),
            },
            'testnet': {
                'evm':  app.config.get('FEE_WALLET_EVM_TESTNET',  ''),
                'tvm':  app.config.get('FEE_WALLET_TRON_TESTNET', ''),
                'utxo': app.config.get('FEE_WALLET_BTC_TESTNET',  ''),
            },
        }

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
        logger.info("OrderProcessor loop starting (PID=%s, thread=%s)",
                     os.getpid(), threading.current_thread().name)
        # On startup, reset any orders stuck mid-flight from a previous crash
        with self.app.app_context():
            stuck = MixOrder.query.filter(
                MixOrder.status.in_(['depositing', 'withdrawing'])
            ).all()
            for o in stuck:
                logger.warning(
                    "Resetting stuck order %s from '%s' → 'payment_confirmed'", o.id, o.status
                )
                o.status = 'payment_confirmed'
            if stuck:
                db.session.commit()

        cycle = 0
        while True:
            try:
                with self.app.app_context():
                    self.process_detected_payments()
                    self.process_confirmed_payments()
                    self.process_deposited_orders()
                    self.expire_stale_orders()
                cycle += 1
                if cycle % 30 == 1:  # log heartbeat every ~5 minutes
                    logger.info("OrderProcessor heartbeat — cycle %d", cycle)
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
                    self._release_reserved_units(order)
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
                        self._release_reserved_units(order)
                        db.session.commit()
                        logger.warning(f"Order {order.id}: payment failed — {error}")
                        continue
                    # Log non-fatal errors so they don't silently swallow
                    logger.warning(
                        f"Order {order.id}: payment verification issue — {error} "
                        f"(will retry)"
                    )
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
                else:
                    confs = verification.get('confirmations', 0)
                    verified = verification.get('verified', False)
                    logger.debug(
                        f"Order {order.id}: waiting — verified={verified}, "
                        f"confirmations={confs}/{pool.min_confirmations}"
                    )

            except Exception as e:
                logger.error(f"Order {order.id}: error verifying payment — {e}", exc_info=True)
                order.status = 'failed'
                order.error_message = f"Payment verification error: {str(e)}"
                self._release_reserved_units(order)
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
                    self._release_reserved_units(order)
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
                self._release_reserved_units(order)
                db.session.commit()

    def _process_btc_withdrawal(self, order: MixOrder):
        """Handle BTC order: zkSNARK anchor proof on Ethereum + BTC payout.

        Flow:
        1. If a BTC_ANCHOR pool unit is reserved for this order, generate a
           zkSNARK proof via the anchor contract on Ethereum and publish the
           nullifier on-chain. This gives post-mix privacy analysis.
        2. Send the BTC payout to the recipient's Bitcoin address.
        3. Forward the operator fee to the configured BTC fee wallet.
        """
        try:
            # ----------------------------------------------------------------
            # Step 1: zkSNARK anchor proof on Ethereum (best-effort)
            # ----------------------------------------------------------------
            anchor_data = {}
            anchor_unit = PoolUnit.query.filter_by(
                symbol='BTC_ANCHOR',
                chain='ethereum',
                reserved_for_order=order.id,
                status='reserved',
            ).order_by(PoolUnit.id).first()

            if anchor_unit:
                logger.info(
                    f"Order {order.id}: generating BTC anchor proof "
                    f"(PoolUnit #{anchor_unit.id}, leaf={anchor_unit.leaf_index})"
                )
                order.status = 'proving'
                db.session.commit()

                try:
                    anchor_contract = anchor_unit.mixer_contract
                    anchor_rpc_url = self._get_rpc_url('ethereum', order.network_mode)
                    mixer = get_mixer(order.network_mode)
                    wallet = self._get_wallet()
                    service_addr = wallet.get_evm_address()

                    # Get anchor asset for the adapter
                    anchor_asset = mixer.registry.get_asset('BTC_ANCHOR', 'ethereum')
                    if not anchor_asset:
                        raise RuntimeError("BTC_ANCHOR asset not found in registry")

                    adapter = mixer._get_adapter(anchor_asset)

                    secret = int(anchor_unit.secret)
                    leaf_index = anchor_unit.leaf_index

                    root = adapter.get_root()
                    path, address_bits = adapter.get_path(leaf_index)

                    # ext_hash for anchor: uses the anchor contract + service wallet
                    ext_hash = self._compute_ext_hash(
                        anchor_contract, service_addr, 'evm',
                    )

                    proof_data = mixer._generate_proof(
                        root=root,
                        secret=secret,
                        ext_hash=ext_hash,
                        address_bits=address_bits,
                        path=path,
                        leaf_index=leaf_index,
                    )

                    if proof_data is None:
                        raise RuntimeError("zkSNARK proof generation failed")

                    # Submit anchor withdrawal (1 wei → service wallet, proves nullifier)
                    anchor_result = wallet.withdraw_via_relayer(
                        chain='ethereum',
                        rpc_url=anchor_rpc_url,
                        contract_address=anchor_contract,
                        root=proof_data.root,
                        nullifier=proof_data.nullifier,
                        proof_points=list(proof_data.proof_points),
                        recipient=service_addr,
                        relayer_fee=0,
                        is_native=True,
                        payout_amount=1,  # 1 wei
                        network_mode=order.network_mode,
                    )

                    if not anchor_result.get('success'):
                        raise RuntimeError(anchor_result.get('error', 'anchor withdraw failed'))

                    # Record anchor proof
                    anchor_unit.status = 'withdrawn'
                    anchor_unit.nullifier = str(proof_data.nullifier)
                    anchor_unit.withdraw_tx_hash = anchor_result['tx_hash']
                    anchor_unit.withdrawn_at = datetime.utcnow()
                    order.nullifier = str(proof_data.nullifier)
                    anchor_data = {
                        'anchor_contract': anchor_contract,
                        'anchor_tx': anchor_result['tx_hash'],
                        'nullifier': str(proof_data.nullifier),
                        'leaf_index': leaf_index,
                    }
                    db.session.flush()
                    logger.info(
                        f"Order {order.id}: BTC anchor proof on Ethereum — "
                        f"nullifier={proof_data.nullifier}, tx={anchor_result['tx_hash']}"
                    )

                except Exception as anchor_err:
                    logger.warning(
                        f"Order {order.id}: BTC anchor proof failed (non-fatal) — {anchor_err}"
                    )
                    # Release the anchor unit back to available
                    if anchor_unit.status == 'reserved':
                        anchor_unit.status = 'available'
                        anchor_unit.reserved_for_order = None
                        anchor_unit.reserved_at = None
                    anchor_data = {}

            # ----------------------------------------------------------------
            # Step 2: Send BTC payout to recipient
            # ----------------------------------------------------------------
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

            # Store anchor proof data in unit_data for privacy analysis
            if anchor_data:
                order.unit_data = json.dumps({'btc_anchor': anchor_data})

            order.deposited_at = order.payment_confirmed_at  # time reference for analysis
            db.session.commit()
            logger.info(
                f"Order {order.id}: BTC withdrawal completed ({order.units} units) "
                f"— tx={order.withdraw_tx_hash}"
            )

            # ----------------------------------------------------------------
            # Step 3: Forward fee to external BTC wallet (best-effort)
            # ----------------------------------------------------------------
            fee_wallet = self._fee_wallets.get(order.network_mode, {}).get('utxo', '')
            btc_fee = int(order.commission_amount)
            if btc_fee > 0 and fee_wallet:
                fee_result = wallet.forward_fee(
                    chain=order.chain, rpc_url='',
                    fee_wallet=fee_wallet, amount=btc_fee,
                    network_mode=order.network_mode,
                )
                if fee_result.get('success'):
                    logger.info(
                        f"Order {order.id}: BTC fee {btc_fee} forwarded to "
                        f"{fee_wallet} (tx={fee_result['tx_hash']})"
                    )
                else:
                    logger.warning(
                        f"Order {order.id}: BTC fee forwarding failed — "
                        f"{fee_result.get('error')}"
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
                    self._release_reserved_units(order)
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
                    self._release_reserved_units(order)
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
                    self._release_reserved_units(order)
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
                    proof_points=list(proof_data.proof_points),
                    recipient=order.recipient_address,
                    relayer_fee=relayer_fee,
                    is_native=is_native,
                    payout_amount=denomination_int - relayer_fee,
                    network_mode=order.network_mode,
                )

                if not withdraw_result.get('success'):
                    order.status = 'failed'
                    order.error_message = withdraw_result.get('error', 'Withdrawal failed')
                    self._release_reserved_units(order)
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

                # Forward fee to external wallet (best-effort)
                chain_type = get_chain_type(order.chain)
                fee_wallet = self._fee_wallets.get(order.network_mode, {}).get(chain_type, '')
                if relayer_fee > 0 and fee_wallet:
                    token_address = self._get_token_address(
                        order.symbol, order.chain, order.network_mode,
                    )
                    fee_result = wallet.forward_fee(
                        chain=order.chain, rpc_url=rpc_url,
                        fee_wallet=fee_wallet, amount=relayer_fee,
                        is_native=is_native, token_address=token_address,
                        network_mode=order.network_mode,
                    )
                    if fee_result.get('success'):
                        logger.info(
                            f"Order {order.id}: fee {relayer_fee} forwarded to "
                            f"{fee_wallet} (tx={fee_result['tx_hash']})"
                        )
                    else:
                        logger.warning(
                            f"Order {order.id}: fee forwarding failed — "
                            f"{fee_result.get('error')}"
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
                self._release_reserved_units(order)
                db.session.commit()

    @staticmethod
    def _compute_ext_hash(mixer_contract: str, recipient_address: str,
                          chain_type: str) -> int:
        """Compute ext_hash matching the contract's sha256(abi.encodePacked(...))."""
        if chain_type == 'tvm':
            try:
                from tronpy.keys import to_hex_address
                # to_hex_address returns 21-byte "41..." hex — strip the Tron
                # network-byte prefix to get the 20-byte address that Solidity
                # uses in abi.encodePacked(address(this), msg.sender).
                contract_hex = to_hex_address(mixer_contract)
                recipient_hex = to_hex_address(recipient_address)
                if contract_hex.startswith("41") and len(contract_hex) == 42:
                    contract_hex = contract_hex[2:]
                if recipient_hex.startswith("41") and len(recipient_hex) == 42:
                    recipient_hex = recipient_hex[2:]
                contract_bytes = bytes.fromhex(contract_hex)    # 20 bytes
                recipient_bytes = bytes.fromhex(recipient_hex)  # 20 bytes
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
    # Helper: release reserved units for a failed/expired order
    # ------------------------------------------------------------------

    def _release_reserved_units(self, order: MixOrder):
        """Release any reserved PoolUnits back to 'available' for a given order."""
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
            self._release_reserved_units(order)

            logger.info(f"Order {order.id}: expired (pending since {order.created_at})")

        if stale_orders:
            db.session.commit()
            logger.info(f"Expired {len(stale_orders)} stale order(s)")
