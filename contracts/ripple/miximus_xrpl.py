"""
Miximus XRP Ledger Adapter

For XRP (Ripple) — which has very limited smart contract capabilities.

The XRPL approach uses:
  1. Escrow + Conditional Payments (built-in XRPL features)
  2. Off-chain zkSNARK proof verification via a relay network
  3. XRPL Hooks (if enabled on the network) for on-chain logic

Architecture:
  - Deposits use XRPL EscrowCreate with crypto-conditions
  - A relay network verifies zkSNARK proofs off-chain
  - Withdrawals use EscrowFinish with fulfillment from relay

Also covers: NEM (XEM) and Ontology (ONT) via similar off-chain approaches.

MiMC implementation matches ethsnarks C++ circuit exactly for the
off-chain Merkle tree maintained by relay operators.

Copyright 2024 Miximus Authors — GPL-3.0-or-later
"""

import hashlib
import struct
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


# =========================================================================
#                    MiMC HASH (matching ethsnarks C++ circuit)
# =========================================================================

SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617

# 29 level-specific IVs matching ethsnarks C++ circuit merkle_tree_IVs()
LEVEL_IVS = [
    149674538925118052205057075966660054952481571156186698930522557832224430770,
    9670701465464311903249220692483401938888498641874948577387207195814981706974,
    18318710344500308168304415114839554107298291987930233567781901093928276468271,
    6597209388525824933845812104623007130464197923269180086306970975123437805179,
    21720956803147356712695575768577036859892220417043839172295094119877855004262,
    10330261616520855230513677034606076056972336573153777401182178891807369896722,
    17466547730316258748333298168566143799241073466140136663575045164199607937939,
    18881017304615283094648494495339883533502299318365959655029893746755475886610,
    21580915712563378725413940003372103925756594604076607277692074507345076595494,
    12316305934357579015754723412431647910012873427291630993042374701002287130550,
    18905410889238873726515380969411495891004493295170115920825550288019118582494,
    12819107342879320352602391015489840916114959026915005817918724958237245903353,
    8245796392944118634696709403074300923517437202166861682117022548371601758802,
    16953062784314687781686527153155644849196472783922227794465158787843281909585,
    19346880451250915556764413197424554385509847473349107460608536657852472800734,
    14486794857958402714787584825989957493343996287314210390323617462452254101347,
    11127491343750635061768291849689189917973916562037173191089384809465548650641,
    12217916643258751952878742936579902345100885664187835381214622522318889050675,
    722025110834410790007814375535296040832778338853544117497481480537806506496,
    15115624438829798766134408951193645901537753720219896384705782209102859383951,
    11495230981884427516908372448237146604382590904456048258839160861769955046544,
    16867999085723044773810250829569850875786210932876177117428755424200948460050,
    1884116508014449609846749684134533293456072152192763829918284704109129550542,
    14643335163846663204197941112945447472862168442334003800621296569318670799451,
    1933387276732345916104540506251808516402995586485132246682941535467305930334,
    7286414555941977227951257572976885370489143210539802284740420664558593616067,
    16932161189449419608528042274282099409408565503929504242784173714823499212410,
    16562533130736679030886586765487416082772837813468081467237161865787494093536,
    6037428193077828806710267464232314380014232668931818917272972397574634037180,
]


def _keccak256(data: bytes) -> bytes:
    """Keccak-256 hash (same as Ethereum/ethsnarks, NOT SHA3-256)."""
    try:
        from Crypto.Hash import keccak as keccak_mod
        return keccak_mod.new(data=data, digest_bits=256).digest()
    except ImportError:
        try:
            import sha3
            k = sha3.keccak_256()
            k.update(data)
            return k.digest()
        except ImportError:
            # Fallback: use pysha3 or web3
            try:
                from web3 import Web3
                return Web3.keccak(data)
            except ImportError:
                raise ImportError(
                    "Install pycryptodome, pysha3, or web3 for keccak256 support"
                )


def _compute_mimc_constants() -> list:
    """Compute 91 MiMC round constants from keccak256 hash chain."""
    seed = _keccak256(b"mimc")
    constants = []
    c = seed
    for _ in range(91):
        c = _keccak256(c)
        constants.append(int.from_bytes(c, 'big') % SCALAR_FIELD)
    return constants


# Lazily computed
_MIMC_CONSTANTS = None


def _get_mimc_constants():
    global _MIMC_CONSTANTS
    if _MIMC_CONSTANTS is None:
        _MIMC_CONSTANTS = _compute_mimc_constants()
    return _MIMC_CONSTANTS


def mimc_cipher(x: int, k: int) -> int:
    """MiMC-p/p cipher: 91 rounds, x^7 exponent, matching ethsnarks C++."""
    constants = _get_mimc_constants()
    for i in range(91):
        t = (x + constants[i] + k) % SCALAR_FIELD
        t2 = (t * t) % SCALAR_FIELD
        t4 = (t2 * t2) % SCALAR_FIELD
        x = (t * t2 * t4) % SCALAR_FIELD
    return (x + k) % SCALAR_FIELD


def mimc_hash(data: list, iv: int = 0) -> int:
    """MiMC hash with Miyaguchi-Preneel compression, matching ethsnarks."""
    r = iv
    for x in data:
        h = mimc_cipher(x, r)
        r = (r + x + h) % SCALAR_FIELD
    return r


# =========================================================================
#                    MERKLE TREE (full node storage)
# =========================================================================

TREE_DEPTH = 29


class MerkleTree:
    """Full Merkle tree with level-specific IVs matching ethsnarks circuit."""

    def __init__(self):
        self.tree_nodes: Dict[Tuple[int, int], int] = {}  # (level, index) -> hash
        self.zero_hashes: List[int] = []
        self.next_leaf_index = 0

        # Compute zero hashes with level IVs
        zero = 0
        for i in range(TREE_DEPTH):
            self.zero_hashes.append(zero)
            zero = mimc_hash([zero, zero], LEVEL_IVS[i])
        self.current_root = zero

    def get_node(self, level: int, index: int) -> int:
        val = self.tree_nodes.get((level, index), 0)
        if val != 0:
            return val
        return self.zero_hashes[level]

    def insert_leaf(self, leaf: int) -> int:
        """Insert a leaf and return the new root."""
        index = self.next_leaf_index
        self.next_leaf_index += 1

        self.tree_nodes[(0, index)] = leaf
        current_node = leaf
        idx = index

        for level in range(TREE_DEPTH):
            parent_idx = idx // 2
            if idx % 2 == 0:
                left = current_node
                right = self.get_node(level, idx + 1)
            else:
                left = self.get_node(level, idx - 1)
                right = current_node

            current_node = mimc_hash([left, right], LEVEL_IVS[level])
            self.tree_nodes[(level + 1, parent_idx)] = current_node
            idx = parent_idx

        self.current_root = current_node
        return current_node

    def get_path(self, leaf_index: int) -> Tuple[List[int], List[bool]]:
        """Get authentication path for a leaf."""
        path = []
        address_bits = []
        for i in range(TREE_DEPTH):
            node_idx = leaf_index >> i
            address_bits.append(node_idx & 1 == 1)
            sibling_idx = node_idx ^ 1
            path.append(self.get_node(i, sibling_idx))
        return path, address_bits


# =========================================================================
#                    CHAIN CONFIGURATIONS
# =========================================================================

class LimitedChain(Enum):
    """Chains with limited/no smart contract support that use off-chain verification"""
    RIPPLE = "xrp"
    NEM = "xem"
    ONTOLOGY = "ont"


CHAIN_CONFIG = {
    LimitedChain.RIPPLE: {
        "name": "XRP Ledger",
        "symbol": "XRP",
        "decimals": 6,
        "denomination": 1_000_000,  # 1 XRP in drops
        "method": "escrow",
        "min_confirmations": 1,
        "ledger_close_time": 4,  # seconds
    },
    LimitedChain.NEM: {
        "name": "NEM",
        "symbol": "XEM",
        "decimals": 6,
        "denomination": 1_000_000,  # 1 XEM in micro XEM
        "method": "multisig",
        "min_confirmations": 1,
    },
    LimitedChain.ONTOLOGY: {
        "name": "Ontology",
        "symbol": "ONT",
        "decimals": 0,
        "denomination": 1,  # 1 ONT (indivisible)
        "method": "neovm",  # Ontology uses NeoVM
        "min_confirmations": 1,
    },
}


# =========================================================================
#                    DATA CLASSES
# =========================================================================

@dataclass
class EscrowDeposit:
    """XRP Ledger Escrow-based deposit"""
    chain: LimitedChain
    escrow_id: str
    amount: int
    condition: str  # Crypto-Condition (SHA-256)
    cancel_after: int  # Ledger index for timeout
    sender: str
    leaf_hash: int
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


@dataclass
class RelayWithdrawal:
    """Off-chain verified withdrawal"""
    chain: LimitedChain
    root: int
    nullifier: int
    proof_json: str
    recipient: str
    relay_signatures: List[str] = field(default_factory=list)
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


# =========================================================================
#                    XRPL MIXER
# =========================================================================

class MiximusXRPL:
    """
    XRPL-based mixer using Escrow + off-chain proof verification.

    XRPL Escrow Flow:
    1. Depositor creates an EscrowCreate transaction with:
       - Amount: denomination (e.g., 1 XRP)
       - Condition: SHA-256 crypto-condition derived from leaf_hash
       - CancelAfter: timeout for refund
       - Destination: relay multi-sig account

    2. Relay network monitors for valid escrows and adds leaves to Merkle tree

    3. Withdrawer generates zkSNARK proof and submits to relay

    4. Relay verifies proof, generates fulfillment, and executes EscrowFinish
       to release funds to the recipient
    """

    def __init__(self, relay_operators: List[str] = None, threshold: int = 2):
        self.chain = LimitedChain.RIPPLE
        self.config = CHAIN_CONFIG[self.chain]
        self.relay_operators = relay_operators or []
        self.threshold = threshold

        self.merkle_tree = MerkleTree()
        self.nullifiers: set = set()
        self.roots: set = set()
        self.deposits: Dict[str, EscrowDeposit] = {}
        self.withdrawals: Dict[str, RelayWithdrawal] = {}

        # Record initial root
        self.roots.add(self.merkle_tree.current_root)

    def create_escrow_deposit(self, leaf_hash: int, sender: str,
                               current_ledger: int) -> dict:
        """
        Generate an XRPL EscrowCreate transaction template.

        Returns a transaction dict ready to be signed and submitted.
        """
        # Create crypto-condition from leaf hash
        leaf_bytes = leaf_hash.to_bytes(32, 'big')
        preimage = leaf_bytes  # The fulfillment
        condition = hashlib.sha256(
            b'\xa0\x22\x80\x20' + hashlib.sha256(preimage).digest()
        ).digest()
        condition_hex = condition.hex().upper()

        # Escrow timeout (24 hours in ledger indices, ~4 sec each)
        cancel_after_ledger = current_ledger + (24 * 60 * 60 // 4)

        tx = {
            "TransactionType": "EscrowCreate",
            "Account": sender,
            "Destination": self._get_relay_account(),
            "Amount": str(self.config["denomination"]),
            "Condition": condition_hex,
            "CancelAfter": cancel_after_ledger,
            "Memos": [
                {
                    "Memo": {
                        "MemoType": "746578742F706C61696E",  # text/plain
                        "MemoData": hex(leaf_hash)[2:].upper(),
                    }
                }
            ],
        }

        deposit = EscrowDeposit(
            chain=self.chain,
            escrow_id="",
            amount=self.config["denomination"],
            condition=condition_hex,
            cancel_after=cancel_after_ledger,
            sender=sender,
            leaf_hash=leaf_hash,
        )

        return {
            "transaction": tx,
            "deposit": deposit,
            "fulfillment_preimage": preimage.hex(),
        }

    def create_batch_escrow_deposit(self, leaf_hashes: list, sender: str,
                                     current_ledger: int) -> list:
        """
        Create N escrow deposits in a single batch.
        Returns a list of transaction dicts — each is an EscrowCreate.
        """
        if len(leaf_hashes) == 0 or len(leaf_hashes) > 20:
            raise ValueError("Batch size must be 1-20")

        results = []
        for leaf_hash in leaf_hashes:
            result = self.create_escrow_deposit(leaf_hash, sender, current_ledger)
            results.append(result)
        return results

    def confirm_escrow(self, deposit: EscrowDeposit, escrow_id: str) -> bool:
        """
        Called after the HTLC funding transaction is confirmed on-chain.
        Adds the leaf to the off-chain Merkle tree.
        """
        deposit.escrow_id = escrow_id
        deposit.status = "confirmed"
        self.deposits[escrow_id] = deposit

        # Add leaf to Merkle tree (proper MiMC with level IVs)
        new_root = self.merkle_tree.insert_leaf(deposit.leaf_hash)
        self.roots.add(new_root)

        return True

    def request_withdrawal(self, root: int, nullifier: int,
                           proof_json: str, recipient: str) -> RelayWithdrawal:
        """Submit withdrawal request with proof to relay network."""
        if nullifier in self.nullifiers:
            raise ValueError("Nullifier already spent (double-spend attempt)")
        if root not in self.roots:
            raise ValueError("Unknown Merkle root")

        withdrawal = RelayWithdrawal(
            chain=self.chain,
            root=root,
            nullifier=nullifier,
            proof_json=proof_json,
            recipient=recipient,
        )

        return withdrawal

    def batch_withdraw_escrows(self, withdrawals: list) -> list:
        """
        Create a batch of withdrawal requests (up to 5).

        Each entry in withdrawals should be a dict with keys:
          root, nullifier, proof_json, recipient

        Returns a list of RelayWithdrawal objects.
        """
        count = len(withdrawals)
        if count <= 0 or count > 5:
            raise ValueError("Batch size must be 1-5")

        results = []
        for w in withdrawals:
            root = w["root"]
            nullifier = w["nullifier"]
            proof_json = w["proof_json"]
            recipient = w["recipient"]

            if nullifier in self.nullifiers:
                raise ValueError(
                    f"Nullifier already spent (double-spend attempt): {nullifier}"
                )
            if root not in self.roots:
                raise ValueError(f"Unknown Merkle root: {root}")

            withdrawal = RelayWithdrawal(
                chain=self.chain,
                root=root,
                nullifier=nullifier,
                proof_json=proof_json,
                recipient=recipient,
            )
            results.append(withdrawal)

        return results

    def relay_approve(self, withdrawal: RelayWithdrawal, signature: str) -> bool:
        """Relay operator approves withdrawal after verifying proof."""
        withdrawal.relay_signatures.append(signature)
        if len(withdrawal.relay_signatures) >= self.threshold:
            withdrawal.status = "approved"
            self.nullifiers.add(withdrawal.nullifier)
            return True
        return False

    def generate_escrow_finish(self, withdrawal: RelayWithdrawal,
                                escrow_id: str, fulfillment: str) -> dict:
        """
        Generate EscrowFinish transaction to release funds.
        Called after relay operators have verified the proof.
        """
        deposit = self.deposits.get(escrow_id)
        if not deposit:
            raise ValueError("Unknown escrow")

        tx = {
            "TransactionType": "EscrowFinish",
            "Account": self._get_relay_account(),
            "Owner": deposit.sender,
            "OfferSequence": int(escrow_id),
            "Condition": deposit.condition,
            "Fulfillment": fulfillment,
        }

        return tx

    def get_path(self, leaf_index: int) -> Tuple[List[int], List[bool]]:
        """Get Merkle authentication path for a leaf."""
        return self.merkle_tree.get_path(leaf_index)

    def hash_public_inputs(self, root: int, nullifier: int, ext_hash: int) -> int:
        """Hash public inputs for proof generation."""
        return mimc_hash([root, nullifier, ext_hash])

    def make_leaf_hash(self, secret: int) -> int:
        """Compute leaf hash from secret."""
        return mimc_hash([secret])

    def _get_relay_account(self) -> str:
        """Get the multi-sig relay account address"""
        return "rMiximusRelayMultiSig..."  # Placeholder

    def get_status(self) -> dict:
        return {
            "chain": self.config["name"],
            "symbol": self.config["symbol"],
            "denomination": self.config["denomination"],
            "total_deposits": len(self.deposits),
            "total_nullifiers": len(self.nullifiers),
            "next_leaf_index": self.merkle_tree.next_leaf_index,
            "current_root": hex(self.merkle_tree.current_root),
        }


# =========================================================================
#                    NEM MIXER
# =========================================================================

class MiximusNEM:
    """
    NEM-based mixer using multisig aggregate transactions.

    NEM uses multisig accounts with aggregate bonded transactions.
    The relay operators form a multisig account that holds deposited XEM.
    """

    def __init__(self, relay_operators: List[str] = None, threshold: int = 2):
        self.config = CHAIN_CONFIG[LimitedChain.NEM]
        self.relay_operators = relay_operators or []
        self.threshold = threshold
        self.merkle_tree = MerkleTree()
        self.nullifiers: set = set()
        self.roots: set = set()
        self.roots.add(self.merkle_tree.current_root)

    def create_deposit(self, leaf_hash: int, sender: str) -> dict:
        """Generate NEM transfer to multisig with leaf hash in message"""
        return {
            "type": "transfer",
            "recipient": self._get_multisig_account(),
            "amount": self.config["denomination"],
            "message": f"miximus:{hex(leaf_hash)}",
        }

    def confirm_deposit(self, leaf_hash: int) -> int:
        """Confirm deposit and add to Merkle tree."""
        new_root = self.merkle_tree.insert_leaf(leaf_hash)
        self.roots.add(new_root)
        return new_root

    def request_withdrawal(self, root: int, nullifier: int,
                           proof_json: str, recipient: str) -> dict:
        """Request withdrawal — relays verify proof and co-sign multisig tx"""
        if nullifier in self.nullifiers:
            raise ValueError("Nullifier already spent")
        if root not in self.roots:
            raise ValueError("Unknown Merkle root")
        return {
            "type": "aggregate_bonded",
            "inner_transactions": [{
                "type": "transfer",
                "signer": self._get_multisig_account(),
                "recipient": recipient,
                "amount": self.config["denomination"],
                "message": f"miximus:withdraw:{hex(nullifier)}",
            }],
        }

    def _get_multisig_account(self) -> str:
        return "MIXIMUS-RELAY-MULTISIG"  # Placeholder


# =========================================================================
#                    ONTOLOGY MIXER
# =========================================================================

class MiximusOntology:
    """
    Ontology-based mixer using NeoVM contracts.

    Ontology supports NeoVM (similar to NEO) and can run smart contracts.
    The approach is similar to the NEO contract but adapted for Ontology's
    native token (ONT, which is indivisible) and ONG (divisible).
    """

    def __init__(self, relay_operators: List[str] = None):
        self.config = CHAIN_CONFIG[LimitedChain.ONTOLOGY]
        self.relay_operators = relay_operators or []
        self.merkle_tree = MerkleTree()
        self.nullifiers: set = set()
        self.roots: set = set()
        self.roots.add(self.merkle_tree.current_root)

    def confirm_deposit(self, leaf_hash: int) -> int:
        """Confirm deposit and add to Merkle tree."""
        new_root = self.merkle_tree.insert_leaf(leaf_hash)
        self.roots.add(new_root)
        return new_root

    def get_contract_template(self) -> str:
        """Return the NeoVM contract template for Ontology"""
        return """
        // Ontology Miximus Contract (NeoVM / Python)
        // Similar structure to NEO contract but uses:
        //   - OntClib for crypto operations
        //   - Native.invoke for ONT/ONG transfers
        //   - GetExecutingScriptHash() for contract address
        //   - MiMC hash matching ethsnarks C++ circuit
        //
        // Deploy via: ontology-python-sdk
        """


# =========================================================================
#                    FACTORY
# =========================================================================

class LimitedChainFactory:
    """Factory for creating mixers on chains with limited smart contract support"""

    @staticmethod
    def create_mixer(chain: LimitedChain, **kwargs):
        if chain == LimitedChain.RIPPLE:
            return MiximusXRPL(**kwargs)
        elif chain == LimitedChain.NEM:
            return MiximusNEM(**kwargs)
        elif chain == LimitedChain.ONTOLOGY:
            return MiximusOntology(**kwargs)
        else:
            raise ValueError(f"Unsupported chain: {chain}")

    @staticmethod
    def supported_chains() -> List[dict]:
        return [
            {"chain": c.value, "name": cfg["name"], "symbol": cfg["symbol"]}
            for c, cfg in CHAIN_CONFIG.items()
        ]
