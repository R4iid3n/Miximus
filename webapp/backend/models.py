"""
SQLAlchemy models for the Miximus custodial mixing service.
"""

import json
import uuid
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class MixOrder(db.Model):
    """Tracks a single mix request through its full lifecycle."""
    __tablename__ = 'mix_orders'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Pool identification
    symbol = db.Column(db.String(20), nullable=False)
    chain = db.Column(db.String(50), nullable=False)
    network_mode = db.Column(db.String(10), nullable=False)

    # User-provided
    recipient_address = db.Column(db.String(128), nullable=False)
    user_tx_hash = db.Column(db.String(128), nullable=True)

    # Service address (hot wallet)
    service_address = db.Column(db.String(128), nullable=False)

    # Amounts (stored as strings for big numbers)
    denomination = db.Column(db.String(80), nullable=False)       # per-unit denomination (wei)
    units = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    total_amount = db.Column(db.String(80), nullable=False, server_default='0')
    commission_rate = db.Column(db.Float, nullable=False, default=0.015)
    commission_amount = db.Column(db.String(80), nullable=True)   # total commission (all units)
    payout_amount = db.Column(db.String(80), nullable=True)       # total payout (all units)

    # Multi-unit processing state
    completed_units = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    current_unit = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    # JSON array: [{secret, leaf_hash, leaf_index, nullifier, deposit_tx_hash, withdraw_tx_hash}, ...]
    unit_data = db.Column(db.Text, nullable=True)

    # Mixer contract interaction (current unit's data for convenience)
    mixer_contract = db.Column(db.String(128), nullable=True)
    secret = db.Column(db.Text, nullable=True)
    leaf_hash = db.Column(db.String(80), nullable=True)
    leaf_index = db.Column(db.Integer, nullable=True)
    nullifier = db.Column(db.String(80), nullable=True)

    # Transaction hashes (latest unit's hashes for quick access)
    deposit_tx_hash = db.Column(db.String(128), nullable=True)
    withdraw_tx_hash = db.Column(db.String(128), nullable=True)

    # Status pipeline:
    # pending_payment -> payment_detected -> payment_confirmed ->
    # depositing -> deposited -> proving -> withdrawing -> completed | failed | expired
    status = db.Column(db.String(30), nullable=False, default='pending_payment')
    error_message = db.Column(db.Text, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    payment_detected_at = db.Column(db.DateTime, nullable=True)
    payment_confirmed_at = db.Column(db.DateTime, nullable=True)
    deposited_at = db.Column(db.DateTime, nullable=True)
    withdrawn_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        db.Index('idx_status', 'status'),
        db.Index('idx_user_tx', 'user_tx_hash'),
        db.Index('idx_symbol_chain', 'symbol', 'chain'),
    )

    def get_unit_data(self) -> list:
        """Parse the unit_data JSON, returning an empty list on failure."""
        if not self.unit_data:
            return []
        try:
            return json.loads(self.unit_data)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_unit_data(self, data: list):
        """Serialize per-unit data to JSON and store it."""
        self.unit_data = json.dumps(data)

    def to_dict(self):
        return {
            'order_id': self.id,
            'symbol': self.symbol,
            'chain': self.chain,
            'network_mode': self.network_mode,
            'recipient_address': self.recipient_address,
            'service_address': self.service_address,
            'denomination': self.denomination,
            'units': self.units,
            'total_amount': self.total_amount,
            'commission_rate': self.commission_rate,
            'commission_amount': self.commission_amount,
            'payout_amount': self.payout_amount,
            'completed_units': self.completed_units,
            'current_unit': self.current_unit,
            'user_tx_hash': self.user_tx_hash,
            'deposit_tx_hash': self.deposit_tx_hash,
            'withdraw_tx_hash': self.withdraw_tx_hash,
            'status': self.status,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'withdrawn_at': self.withdrawn_at.isoformat() if self.withdrawn_at else None,
        }

    def get_steps(self):
        """Return progress steps for frontend display.

        For multi-unit orders, step names include a unit counter
        (e.g. "Депозит в миксер (2/3)") so the user can see per-unit progress.
        """
        status_order = [
            'pending_payment', 'payment_detected', 'payment_confirmed',
            'depositing', 'deposited', 'proving', 'withdrawing', 'completed'
        ]
        current_idx = status_order.index(self.status) if self.status in status_order else -1

        # Build unit-aware step names
        unit_suffix = ''
        if self.units > 1:
            unit_suffix = f' ({self.completed_units}/{self.units})'

        steps = [
            {'name': 'Оплата', 'tx_hash': self.user_tx_hash},
            {'name': f'Депозит в миксер{unit_suffix}', 'tx_hash': self.deposit_tx_hash},
            {'name': f'Генерация доказательства{unit_suffix}', 'tx_hash': None},
            {'name': f'Вывод средств{unit_suffix}', 'tx_hash': self.withdraw_tx_hash},
        ]

        step_thresholds = [2, 4, 5, 7]

        if self.units > 1 and self.completed_units > 0 and self.status != 'completed':
            # Multi-unit in progress: payment is done, middle steps are "in progress",
            # last step is pending until all units complete.
            steps[0]['status'] = 'completed'
            if self.status in ('failed', 'expired'):
                steps[1]['status'] = 'failed'
                steps[2]['status'] = 'pending'
                steps[3]['status'] = 'pending'
            elif self.status in ('depositing',):
                steps[1]['status'] = 'in_progress'
                steps[2]['status'] = 'pending'
                steps[3]['status'] = 'pending'
            elif self.status in ('deposited', 'proving'):
                steps[1]['status'] = 'completed'
                steps[2]['status'] = 'in_progress'
                steps[3]['status'] = 'pending'
            elif self.status in ('withdrawing',):
                steps[1]['status'] = 'completed'
                steps[2]['status'] = 'completed'
                steps[3]['status'] = 'in_progress'
            else:
                # payment_confirmed between units
                steps[1]['status'] = 'in_progress'
                steps[2]['status'] = 'pending'
                steps[3]['status'] = 'pending'
        else:
            # Single-unit or first unit or completed — use threshold logic
            for i, step in enumerate(steps):
                if self.status in ('failed', 'expired'):
                    step['status'] = 'failed' if current_idx >= step_thresholds[i] - 1 else 'pending'
                elif current_idx >= step_thresholds[i]:
                    step['status'] = 'completed'
                elif current_idx >= step_thresholds[i] - 1:
                    step['status'] = 'in_progress'
                else:
                    step['status'] = 'pending'

        return steps


class PoolUnit(db.Model):
    """Tracks individual units deposited into the mixer contract.

    Each row = one leaf in the Merkle tree with its secret.
    Status lifecycle: available → reserved → withdrawn
    Source: 'seed' (operator pre-deposit) or 'replenish' (deposited as part of a user order).
    """
    __tablename__ = 'pool_units'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Pool identification
    symbol = db.Column(db.String(20), nullable=False)
    chain = db.Column(db.String(50), nullable=False)
    network_mode = db.Column(db.String(10), nullable=False)

    # Cryptographic data (secret is kept so we can prove & withdraw later)
    secret = db.Column(db.Text, nullable=False)
    leaf_hash = db.Column(db.String(80), nullable=False)
    leaf_index = db.Column(db.Integer, nullable=False)
    nullifier = db.Column(db.String(80), nullable=True)

    # On-chain references
    mixer_contract = db.Column(db.String(128), nullable=False)
    deposit_tx_hash = db.Column(db.String(128), nullable=False)
    withdraw_tx_hash = db.Column(db.String(128), nullable=True)

    # Status: available → reserved → withdrawn
    status = db.Column(db.String(20), nullable=False, default='available')

    # Reservation — which order will withdraw this unit
    reserved_for_order = db.Column(db.String(36), nullable=True)

    # Source tracking
    source = db.Column(db.String(20), nullable=False, default='seed')  # 'seed' or 'replenish'
    source_order_id = db.Column(db.String(36), nullable=True)  # order that deposited this unit

    # Timestamps
    deposited_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    reserved_at = db.Column(db.DateTime, nullable=True)
    withdrawn_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('idx_pool_unit_status', 'symbol', 'chain', 'network_mode', 'status'),
        db.Index('idx_pool_unit_order', 'reserved_for_order'),
    )


class PoolConfig(db.Model):
    """Operator configuration for each mixer pool."""
    __tablename__ = 'pool_configs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(db.String(20), nullable=False)
    chain = db.Column(db.String(50), nullable=False)
    network_mode = db.Column(db.String(10), nullable=False)
    mixer_contract = db.Column(db.String(128), nullable=False)
    denomination = db.Column(db.String(80), nullable=False)
    commission_rate = db.Column(db.Float, nullable=False, default=0.015)
    service_wallet_address = db.Column(db.String(128), nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    min_confirmations = db.Column(db.Integer, default=3)

    __table_args__ = (
        db.UniqueConstraint('symbol', 'chain', 'network_mode', name='uq_pool'),
    )

    def to_dict(self):
        return {
            'symbol': self.symbol,
            'chain': self.chain,
            'network_mode': self.network_mode,
            'mixer_contract': self.mixer_contract,
            'denomination': self.denomination,
            'commission_rate': self.commission_rate,
            'service_wallet_address': self.service_wallet_address,
            'enabled': self.enabled,
            'min_confirmations': self.min_confirmations,
        }
