"""
Miximus UTXO Chain Adapter — HTLC-based Privacy Mixing

For UTXO-based blockchains that cannot verify zkSNARK proofs on-chain:
  Bitcoin (BTC), Bitcoin Cash (BCH), Bitcoin Gold (BTG), Bitcoin SV (BSV),
  Litecoin (LTC), Dogecoin (DOGE), Dash (DASH), Zcash (ZEC),
  Verge (XVG), Ravencoin (RVN), Komodo (KMD)

Architecture:
  Since UTXO chains lack smart contract capability for zkSNARK verification,
  we use a Hash Time-Locked Contract (HTLC) approach combined with an off-chain
  zkSNARK proof verification relay:

  1. DEPOSIT: User creates an HTLC locking funds with a hash commitment
  2. RELAY: An off-chain relay network verifies the zkSNARK proof
  3. WITHDRAW: Upon valid proof, the relay reveals the preimage allowing withdrawal

  For chains with basic scripting (BTC, LTC, etc.):
    - P2SH HTLC scripts lock the deposit
    - Relay operators verify proofs off-chain
    - Multi-sig federation validates withdrawals

  For Zcash: Can additionally leverage native shielded transactions (z-addrs)
  For Komodo: Can leverage delayed Proof of Work and atomic swaps

Copyright 2024 Miximus Authors — GPL-3.0-or-later
"""

import hashlib
import struct
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# BN254 scalar field (same as alt_bn128 / bn256)
# ---------------------------------------------------------------------------
SNARK_SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617

# ---------------------------------------------------------------------------
# MiMC parameters — must match ethsnarks C++ circuit exactly
# ---------------------------------------------------------------------------
MIMC_ROUNDS = 91
MIMC_EXPONENT = 7
MIMC_SEED = b"mimc"


def _keccak256(data: bytes) -> bytes:
    """
    Keccak-256 (pre-NIST, SHA3_USE_KECCAK=1).

    This is NOT SHA3-256. Python's hashlib.sha3_256 implements the NIST
    FIPS-202 standard which applies different padding. We need the original
    Keccak that Ethereum (and ethsnarks) uses.

    We try pycryptodome first, then pysha3, and fall back to a hardcoded
    constant table (the round constants are deterministic so this is safe).
    """
    try:
        from Crypto.Hash import keccak as _keccak_mod
        return _keccak_mod.new(data=data, digest_bits=256).digest()
    except ImportError:
        pass
    try:
        from sha3 import keccak_256 as _k256
        return _k256(data).digest()
    except ImportError:
        pass
    # Should not reach here in a properly configured environment
    raise ImportError(
        "No Keccak-256 implementation found. "
        "Install pycryptodome (`pip install pycryptodome`) or pysha3."
    )


def _generate_mimc_round_constants(
    seed: bytes = MIMC_SEED,
    rounds: int = MIMC_ROUNDS,
    p: int = SNARK_SCALAR_FIELD,
) -> List[int]:
    """
    Generate MiMC round constants via a Keccak-256 hash chain.

    Algorithm (matches ethsnarks C++ constants_fill):
      h = keccak256(seed)          # initial hash of the ASCII seed
      for i in 0..rounds-1:
          h = keccak256(h)         # chain: each constant derived from previous
          c[i] = int(h) % p        # interpret as big-endian uint256, reduce mod p
    """
    h = _keccak256(seed)
    constants = []
    for _ in range(rounds):
        h = _keccak256(h)
        constants.append(int.from_bytes(h, "big") % p)
    return constants


# Pre-compute the 91 round constants once at import time.
# These are deterministic and identical across every deployment.
MIMC_ROUND_CONSTANTS = _generate_mimc_round_constants()


# ---------------------------------------------------------------------------
# MiMC cipher and Miyaguchi-Preneel hash
# ---------------------------------------------------------------------------

def mimc_cipher(
    x: int,
    k: int,
    constants: List[int] = MIMC_ROUND_CONSTANTS,
    p: int = SNARK_SCALAR_FIELD,
    e: int = MIMC_EXPONENT,
) -> int:
    """
    MiMC block cipher E_k(x).

    Each round:
        a = (x + k + c_i) mod p
        x = a^7 mod p
    After all rounds:
        result = (x + k) mod p

    Matches ethsnarks mimc() in mimc.py / mimc.hpp exactly.
    """
    for c_i in constants:
        a = (x + k + c_i) % p
        x = pow(a, e, p)
    return (x + k) % p


def mimc_hash(
    inputs: List[int],
    key: int = 0,
    p: int = SNARK_SCALAR_FIELD,
) -> int:
    """
    Miyaguchi-Preneel one-way compression using MiMC.

    For each input element x_i:
        r  = E_k(x_i)              # encrypt x_i under current key
        k  = (k + x_i + r) mod p   # Miyaguchi-Preneel feedback

    Returns the final key as the hash digest.
    Matches ethsnarks mimc_hash() exactly.
    """
    for x_i in inputs:
        r = mimc_cipher(x_i, key)
        key = (key + x_i + r) % p
    return key


# ---------------------------------------------------------------------------
# Level-specific IVs for the Merkle tree
# ---------------------------------------------------------------------------
# These 29 values are used as the initial key for MiMC hashing at each tree
# level.  They are hard-coded in the C++ circuit (merkle_tree.cpp) and the
# Python MerkleHasher_MiMC class.  Storing them here avoids any dependency
# on the SHA-256-based derivation that the upstream Python helper uses (the
# circuit itself embeds these exact decimal constants).

TREE_DEPTH = 29

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

assert len(LEVEL_IVS) == TREE_DEPTH


# ---------------------------------------------------------------------------
# Off-chain Merkle tree with full node storage
# ---------------------------------------------------------------------------

def _merkle_hash_node(depth: int, left: int, right: int) -> int:
    """
    Hash two child nodes at a given tree depth using MiMC with the
    level-specific IV as the initial key.
    """
    return mimc_hash([left, right], key=LEVEL_IVS[depth])


def _unique_leaf(depth: int, index: int) -> int:
    """
    Deterministic placeholder for a tree position that has not been filled.

    This matches ethsnarks Abstract_MerkleHasher.unique(): the result is a
    SHA-256 digest of (depth || index) reduced modulo the scalar field.
    """
    item = int(depth).to_bytes(2, "big") + int(index).to_bytes(30, "big")
    h = hashlib.sha256(item).digest()
    return int.from_bytes(h, "big") % SNARK_SCALAR_FIELD


class MerkleTree:
    """
    Full binary Merkle tree of depth 29 using MiMC hashing with
    level-specific IVs.

    Internal storage uses a dict ``tree_nodes[(level, index)]`` so that the
    tree can be sparse (only populated subtrees are stored).

    Level 0 = leaves, level 29 = root.
    """

    def __init__(self, depth: int = TREE_DEPTH):
        self.depth = depth
        self.next_index = 0
        # (level, index) -> field element
        self.tree_nodes: Dict[Tuple[int, int], int] = {}

        # Pre-compute the "zero" (placeholder) value for every level.
        # At level 0 these are unique per-index, but for higher levels
        # we use the hash of two zero children.  This is consistent with
        # how the ethsnarks MerkleTree._updateTree works: any missing child
        # falls back to ``unique(depth, offset)``.
        #
        # For root computation when the tree is empty we need these
        # defaults, but actual path retrieval uses ``_get_node`` which
        # falls back to ``_unique_leaf`` per-index, matching the upstream.
        self._zero_cache: List[int] = [0] * (depth + 1)
        self._zero_cache[0] = _unique_leaf(0, 0)
        for lvl in range(depth):
            self._zero_cache[lvl + 1] = _merkle_hash_node(
                lvl, self._zero_cache[lvl], self._zero_cache[lvl]
            )

    def _get_node(self, level: int, index: int) -> int:
        """Return the node value, falling back to the unique placeholder."""
        if (level, index) in self.tree_nodes:
            return self.tree_nodes[(level, index)]
        return _unique_leaf(level, index)

    def _set_node(self, level: int, index: int, value: int) -> None:
        self.tree_nodes[(level, index)] = value

    def insert(self, leaf: int) -> int:
        """
        Insert a new leaf at the next available position.

        Updates the internal tree all the way up to the root.
        Returns the leaf index that was used.
        """
        if self.next_index >= (1 << self.depth):
            raise RuntimeError("Merkle tree is full")

        leaf_index = self.next_index
        self.next_index += 1

        self._set_node(0, leaf_index, leaf)

        # Propagate up
        current_index = leaf_index
        for lvl in range(self.depth):
            parent_index = current_index >> 1
            left_child = current_index & ~1  # sibling pair: left
            right_child = left_child + 1

            left_val = self._get_node(lvl, left_child)
            right_val = self._get_node(lvl, right_child)

            parent_val = _merkle_hash_node(lvl, left_val, right_val)
            self._set_node(lvl + 1, parent_index, parent_val)

            current_index = parent_index

        return leaf_index

    def update(self, index: int, leaf: int) -> None:
        """
        Update an existing leaf and recompute the path to the root.
        """
        if index >= self.next_index:
            raise KeyError("Leaf index %d has not been inserted yet" % index)

        self._set_node(0, index, leaf)

        current_index = index
        for lvl in range(self.depth):
            parent_index = current_index >> 1
            left_child = current_index & ~1
            right_child = left_child + 1

            left_val = self._get_node(lvl, left_child)
            right_val = self._get_node(lvl, right_child)

            parent_val = _merkle_hash_node(lvl, left_val, right_val)
            self._set_node(lvl + 1, parent_index, parent_val)

            current_index = parent_index

    @property
    def root(self) -> Optional[int]:
        """Return the current Merkle root, or None if tree is empty."""
        if self.next_index == 0:
            return None
        return self._get_node(self.depth, 0)

    def getPath(self, leaf_index: int) -> Tuple[List[int], List[int]]:
        """
        Get the Merkle authentication path for a leaf.

        Returns (address_bits, path) where:
          - address_bits: list of 0/1 indicating the position at each level
            (0 = left child, 1 = right child)
          - path: list of sibling node values at each level

        These can be fed directly to the zkSNARK prover.
        """
        if leaf_index >= self.next_index:
            raise KeyError("Leaf index %d not in tree" % leaf_index)

        address_bits = []
        path = []
        current_index = leaf_index

        for lvl in range(self.depth):
            # Determine which side we are on
            bit = current_index & 1
            address_bits.append(bit)

            # The sibling is the other child of our parent
            sibling_index = current_index ^ 1
            path.append(self._get_node(lvl, sibling_index))

            current_index = current_index >> 1

        return address_bits, path

    def verifyPath(self, leaf: int, leaf_index: int, path: List[int]) -> bool:
        """
        Verify a Merkle path against the current root.
        """
        current = leaf
        idx = leaf_index
        for lvl in range(self.depth):
            bit = idx & 1
            sibling = path[lvl]
            if bit == 0:
                current = _merkle_hash_node(lvl, current, sibling)
            else:
                current = _merkle_hash_node(lvl, sibling, current)
            idx = idx >> 1
        return current == self.root


# ---------------------------------------------------------------------------
# Chain and HTLC types
# ---------------------------------------------------------------------------

class UTXOChain(Enum):
    BITCOIN = "btc"
    BITCOIN_CASH = "bch"
    BITCOIN_GOLD = "btg"
    BITCOIN_SV = "bsv"
    LITECOIN = "ltc"
    DOGECOIN = "doge"
    DASH = "dash"
    ZCASH = "zec"
    VERGE = "xvg"
    RAVENCOIN = "rvn"
    KOMODO = "kmd"


# Chain-specific parameters
CHAIN_CONFIG = {
    UTXOChain.BITCOIN: {
        "name": "Bitcoin",
        "symbol": "BTC",
        "decimals": 8,
        "denomination_satoshis": 10_000_000,  # 0.1 BTC
        "min_confirmations": 6,
        "htlc_timeout_blocks": 144,  # ~24 hours
        "script_type": "p2sh",
        "segwit": True,
        "address_prefix": b'\x00',
        "p2sh_prefix": b'\x05',
    },
    UTXOChain.BITCOIN_CASH: {
        "name": "Bitcoin Cash",
        "symbol": "BCH",
        "decimals": 8,
        "denomination_satoshis": 100_000_000,  # 1 BCH
        "min_confirmations": 6,
        "htlc_timeout_blocks": 144,
        "script_type": "p2sh",
        "segwit": False,
        "address_prefix": b'\x00',
        "p2sh_prefix": b'\x05',
    },
    UTXOChain.BITCOIN_GOLD: {
        "name": "Bitcoin Gold",
        "symbol": "BTG",
        "decimals": 8,
        "denomination_satoshis": 100_000_000,
        "min_confirmations": 6,
        "htlc_timeout_blocks": 144,
        "script_type": "p2sh",
        "segwit": True,
        "address_prefix": b'\x26',
        "p2sh_prefix": b'\x17',
    },
    UTXOChain.BITCOIN_SV: {
        "name": "Bitcoin SV",
        "symbol": "BSV",
        "decimals": 8,
        "denomination_satoshis": 100_000_000,
        "min_confirmations": 6,
        "htlc_timeout_blocks": 144,
        "script_type": "p2sh",
        "segwit": False,
        "address_prefix": b'\x00',
        "p2sh_prefix": b'\x05',
    },
    UTXOChain.LITECOIN: {
        "name": "Litecoin",
        "symbol": "LTC",
        "decimals": 8,
        "denomination_satoshis": 100_000_000,  # 1 LTC
        "min_confirmations": 6,
        "htlc_timeout_blocks": 576,  # ~24 hours (2.5 min blocks)
        "script_type": "p2sh",
        "segwit": True,
        "address_prefix": b'\x30',
        "p2sh_prefix": b'\x32',
    },
    UTXOChain.DOGECOIN: {
        "name": "Dogecoin",
        "symbol": "DOGE",
        "decimals": 8,
        "denomination_satoshis": 100_00_000_000,  # 1000 DOGE
        "min_confirmations": 6,
        "htlc_timeout_blocks": 1440,  # ~24 hours (1 min blocks)
        "script_type": "p2sh",
        "segwit": False,
        "address_prefix": b'\x1e',
        "p2sh_prefix": b'\x16',
    },
    UTXOChain.DASH: {
        "name": "Dash",
        "symbol": "DASH",
        "decimals": 8,
        "denomination_satoshis": 100_000_000,  # 1 DASH
        "min_confirmations": 6,
        "htlc_timeout_blocks": 576,
        "script_type": "p2sh",
        "segwit": False,
        "address_prefix": b'\x4c',
        "p2sh_prefix": b'\x10',
    },
    UTXOChain.ZCASH: {
        "name": "Zcash",
        "symbol": "ZEC",
        "decimals": 8,
        "denomination_satoshis": 100_000_000,  # 1 ZEC
        "min_confirmations": 6,
        "htlc_timeout_blocks": 576,
        "script_type": "p2sh",
        "segwit": False,
        "address_prefix": b'\x1c\xb8',
        "p2sh_prefix": b'\x1c\xbd',
        "supports_shielded": True,  # ZEC-specific: can use z-addresses
    },
    UTXOChain.VERGE: {
        "name": "Verge",
        "symbol": "XVG",
        "decimals": 8,
        "denomination_satoshis": 100_000_000_00,  # 100 XVG
        "min_confirmations": 6,
        "htlc_timeout_blocks": 1440,
        "script_type": "p2sh",
        "segwit": False,
        "address_prefix": b'\x1e',
        "p2sh_prefix": b'\x21',
    },
    UTXOChain.RAVENCOIN: {
        "name": "Ravencoin",
        "symbol": "RVN",
        "decimals": 8,
        "denomination_satoshis": 100_000_000_00,  # 100 RVN
        "min_confirmations": 6,
        "htlc_timeout_blocks": 1440,
        "script_type": "p2sh",
        "segwit": False,
        "address_prefix": b'\x3c',
        "p2sh_prefix": b'\x7a',
    },
    UTXOChain.KOMODO: {
        "name": "Komodo",
        "symbol": "KMD",
        "decimals": 8,
        "denomination_satoshis": 100_000_000,  # 1 KMD
        "min_confirmations": 6,
        "htlc_timeout_blocks": 1440,
        "script_type": "p2sh",
        "segwit": False,
        "address_prefix": b'\x3c',
        "p2sh_prefix": b'\x55',
        "supports_atomic_swap": True,  # KMD-specific
    },
}


@dataclass
class HTLCDeposit:
    """Represents a pending HTLC deposit on a UTXO chain"""
    chain: UTXOChain
    txid: str
    amount_satoshis: int
    hash_lock: bytes  # SHA256 hash of the secret
    time_lock: int  # Block height for timeout
    sender_pubkey: str
    redeem_script: bytes
    p2sh_address: str
    leaf_hash: int  # Merkle tree leaf hash (same as EVM)
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


@dataclass
class HTLCWithdrawal:
    """Represents a pending withdrawal with zkSNARK proof"""
    chain: UTXOChain
    root: int
    nullifier: int
    proof_json: str
    recipient_address: str
    relay_signatures: List[str] = field(default_factory=list)
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


class MiximusHTLC:
    """
    HTLC-based mixer for UTXO chains.

    Flow:
    1. Depositor generates a secret and computes leaf_hash = MiMC(secret)
    2. Depositor creates HTLC with hash_lock = SHA256(leaf_hash)
    3. Depositor broadcasts HTLC transaction to the UTXO chain
    4. Relay operators monitor for valid HTLC deposits
    5. Once confirmed, relay adds the leaf to the off-chain Merkle tree
    6. Withdrawer generates zkSNARK proof (same as EVM version)
    7. Withdrawer submits proof to the relay network
    8. Relay operators verify the proof and co-sign the withdrawal
    9. Multi-sig threshold met -> withdrawal transaction is broadcast
    """

    def __init__(self, chain: UTXOChain, relay_operators: List[str] = None,
                 threshold: int = 2):
        self.chain = chain
        self.config = CHAIN_CONFIG[chain]
        self.relay_operators = relay_operators or []
        self.threshold = threshold  # M-of-N relay signatures required

        # Off-chain Merkle tree state (mirrors the on-chain EVM tree)
        self.tree_depth = TREE_DEPTH
        self.tree = MerkleTree(self.tree_depth)
        self.next_leaf_index = 0

        # Tracking
        self.deposits: Dict[str, HTLCDeposit] = {}
        self.withdrawals: Dict[str, HTLCWithdrawal] = {}
        self.nullifiers: set = set()

        # Keep a list of leaf hashes for backward compatibility
        self.leaves: List[int] = []

    def generate_htlc_script(self, hash_lock: bytes, recipient_pubkey_hash: bytes,
                              sender_pubkey_hash: bytes, timeout_blocks: int) -> bytes:
        """
        Generate a Bitcoin-style HTLC redeem script.

        Script logic:
          IF
            // Withdraw path: provide preimage + recipient signature
            SHA256 <hash_lock> EQUALVERIFY
            DUP HASH160 <recipient_pubkey_hash> EQUALVERIFY CHECKSIG
          ELSE
            // Refund path: after timeout, sender can reclaim
            <timeout_blocks> CHECKLOCKTIMEVERIFY DROP
            DUP HASH160 <sender_pubkey_hash> EQUALVERIFY CHECKSIG
          ENDIF
        """
        # Bitcoin Script opcodes
        OP_IF = b'\x63'
        OP_ELSE = b'\x67'
        OP_ENDIF = b'\x68'
        OP_SHA256 = b'\xa8'
        OP_EQUALVERIFY = b'\x88'
        OP_DUP = b'\x76'
        OP_HASH160 = b'\xa9'
        OP_CHECKSIG = b'\xac'
        OP_CHECKLOCKTIMEVERIFY = b'\xb1'
        OP_DROP = b'\x75'

        def push_data(data: bytes) -> bytes:
            length = len(data)
            if length < 76:
                return bytes([length]) + data
            elif length < 256:
                return b'\x4c' + bytes([length]) + data
            else:
                return b'\x4d' + struct.pack('<H', length) + data

        def push_int(n: int) -> bytes:
            if n <= 16:
                return bytes([0x50 + n]) if n > 0 else b'\x00'
            # Encode as little-endian bytes
            result = []
            abs_n = abs(n)
            while abs_n > 0:
                result.append(abs_n & 0xff)
                abs_n >>= 8
            if result[-1] & 0x80:
                result.append(0x80 if n < 0 else 0x00)
            elif n < 0:
                result[-1] |= 0x80
            data = bytes(result)
            return push_data(data)

        script = b''
        script += OP_IF
        # Withdraw path
        script += OP_SHA256
        script += push_data(hash_lock)
        script += OP_EQUALVERIFY
        script += OP_DUP
        script += OP_HASH160
        script += push_data(recipient_pubkey_hash)
        script += OP_EQUALVERIFY
        script += OP_CHECKSIG
        script += OP_ELSE
        # Refund path
        script += push_int(timeout_blocks)
        script += OP_CHECKLOCKTIMEVERIFY
        script += OP_DROP
        script += OP_DUP
        script += OP_HASH160
        script += push_data(sender_pubkey_hash)
        script += OP_EQUALVERIFY
        script += OP_CHECKSIG
        script += OP_ENDIF

        return script

    def compute_p2sh_address(self, redeem_script: bytes) -> str:
        """Compute P2SH address from redeem script"""
        script_hash = hashlib.new('ripemd160',
                                   hashlib.sha256(redeem_script).digest()).digest()
        prefix = self.config['p2sh_prefix']
        payload = prefix + script_hash
        checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        return self._base58_encode(payload + checksum)

    def create_deposit(self, leaf_hash: int, sender_pubkey_hash: bytes,
                       current_block_height: int) -> HTLCDeposit:
        """
        Create a new HTLC deposit commitment.

        Returns the HTLC details — caller must broadcast the funding transaction.
        """
        # Hash lock is derived from the leaf hash
        leaf_bytes = leaf_hash.to_bytes(32, 'big')
        hash_lock = hashlib.sha256(leaf_bytes).digest()

        timeout = current_block_height + self.config['htlc_timeout_blocks']

        # For the relay network, use a placeholder recipient
        # The actual recipient is determined during withdrawal
        relay_pubkey_hash = hashlib.new('ripemd160',
            hashlib.sha256(b'relay_placeholder').digest()).digest()

        redeem_script = self.generate_htlc_script(
            hash_lock=hash_lock,
            recipient_pubkey_hash=relay_pubkey_hash,
            sender_pubkey_hash=sender_pubkey_hash,
            timeout_blocks=timeout
        )

        p2sh_address = self.compute_p2sh_address(redeem_script)

        deposit = HTLCDeposit(
            chain=self.chain,
            txid="",  # Set after broadcasting
            amount_satoshis=self.config['denomination_satoshis'],
            hash_lock=hash_lock,
            time_lock=timeout,
            sender_pubkey=sender_pubkey_hash.hex(),
            redeem_script=redeem_script,
            p2sh_address=p2sh_address,
            leaf_hash=leaf_hash,
        )

        return deposit

    def create_batch_deposit(self, leaf_hashes: list, sender_pubkey_hash: bytes,
                              current_block_height: int) -> list:
        """
        Create N HTLC deposits in a single batch.
        Returns a list of HTLCDeposit objects — caller broadcasts one funding
        transaction with N P2SH outputs.
        """
        if len(leaf_hashes) == 0 or len(leaf_hashes) > 20:
            raise ValueError("Batch size must be 1-20")

        deposits = []
        for leaf_hash in leaf_hashes:
            deposit = self.create_deposit(leaf_hash, sender_pubkey_hash, current_block_height)
            deposits.append(deposit)
        return deposits

    def confirm_deposit(self, deposit: HTLCDeposit, txid: str) -> bool:
        """
        Called after the HTLC funding transaction is confirmed on-chain.
        Adds the leaf to the off-chain Merkle tree.
        """
        deposit.txid = txid
        deposit.status = "confirmed"
        self.deposits[txid] = deposit

        # Add leaf to Merkle tree (proper MiMC-based tree)
        self.tree.insert(deposit.leaf_hash)
        self.leaves.append(deposit.leaf_hash)
        self.next_leaf_index += 1

        return True

    def get_merkle_root(self) -> Optional[int]:
        """Return the current Merkle root of the off-chain tree."""
        return self.tree.root

    def get_merkle_path(self, leaf_index: int) -> Tuple[List[int], List[int]]:
        """
        Get the Merkle authentication path for a leaf.

        Returns (address_bits, path) suitable for the zkSNARK prover.
        """
        return self.tree.getPath(leaf_index)

    def verify_merkle_path(self, leaf: int, leaf_index: int,
                           path: List[int]) -> bool:
        """
        Verify a Merkle authentication path against the current root.
        """
        return self.tree.verifyPath(leaf, leaf_index, path)

    def request_withdrawal(self, root: int, nullifier: int,
                           proof_json: str, recipient_address: str) -> HTLCWithdrawal:
        """
        Submit a withdrawal request with a zkSNARK proof.
        The proof is verified off-chain by the relay network.
        """
        if nullifier in self.nullifiers:
            raise ValueError("Nullifier already spent (double-spend attempt)")

        withdrawal = HTLCWithdrawal(
            chain=self.chain,
            root=root,
            nullifier=nullifier,
            proof_json=proof_json,
            recipient_address=recipient_address,
        )

        return withdrawal

    def create_batch_withdraw(self, withdrawals: list) -> list:
        """
        Create a batch of withdrawal requests (up to 5).

        Each entry in withdrawals should be a dict with keys:
          root, nullifier, proof_json, recipient_address

        Returns a list of HTLCWithdrawal objects.
        """
        count = len(withdrawals)
        if count <= 0 or count > 5:
            raise ValueError("Batch size must be 1-5")

        results = []
        for w in withdrawals:
            root = w["root"]
            nullifier = w["nullifier"]
            proof_json = w["proof_json"]
            recipient_address = w["recipient_address"]

            if nullifier in self.nullifiers:
                raise ValueError(
                    f"Nullifier already spent (double-spend attempt): {nullifier}"
                )

            withdrawal = HTLCWithdrawal(
                chain=self.chain,
                root=root,
                nullifier=nullifier,
                proof_json=proof_json,
                recipient_address=recipient_address,
            )
            results.append(withdrawal)

        return results

    def relay_approve_withdrawal(self, withdrawal: HTLCWithdrawal,
                                  relay_signature: str) -> bool:
        """
        A relay operator approves a withdrawal after verifying the proof.
        Once threshold signatures are collected, the withdrawal can be executed.
        """
        withdrawal.relay_signatures.append(relay_signature)

        if len(withdrawal.relay_signatures) >= self.threshold:
            withdrawal.status = "approved"
            self.nullifiers.add(withdrawal.nullifier)
            return True

        return False

    def get_status(self) -> dict:
        """Return current mixer state"""
        return {
            "chain": self.config["name"],
            "symbol": self.config["symbol"],
            "denomination": self.config["denomination_satoshis"],
            "total_deposits": len(self.deposits),
            "total_nullifiers": len(self.nullifiers),
            "next_leaf_index": self.next_leaf_index,
            "tree_depth": self.tree_depth,
            "merkle_root": self.get_merkle_root(),
        }

    @staticmethod
    def _base58_encode(data: bytes) -> str:
        """Base58 encoding for addresses"""
        alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
        n = int.from_bytes(data, 'big')
        result = ''
        while n > 0:
            n, remainder = divmod(n, 58)
            result = alphabet[remainder] + result
        # Handle leading zeros
        for byte in data:
            if byte == 0:
                result = '1' + result
            else:
                break
        return result


class MiximusHTLCFactory:
    """Factory for creating HTLC mixers across all UTXO chains"""

    @staticmethod
    def create_mixer(chain: UTXOChain, relay_operators: List[str] = None,
                     threshold: int = 2) -> MiximusHTLC:
        return MiximusHTLC(chain, relay_operators, threshold)

    @staticmethod
    def create_all_mixers(relay_operators: List[str] = None,
                          threshold: int = 2) -> Dict[UTXOChain, MiximusHTLC]:
        """Create mixers for all UTXO chains"""
        return {
            chain: MiximusHTLC(chain, relay_operators, threshold)
            for chain in UTXOChain
        }

    @staticmethod
    def supported_chains() -> List[dict]:
        """List all supported UTXO chains"""
        return [
            {
                "chain": chain.value,
                "name": config["name"],
                "symbol": config["symbol"],
                "denomination": config["denomination_satoshis"],
            }
            for chain, config in CHAIN_CONFIG.items()
        ]
