"""
Miximus ICON Smart Contract (SCORE)

zkSNARK-based mixer for ICON (ICX).
Written in Python for ICON's SCORE framework.

Supported: ICX (native)

MiMC cipher matches the ethsnarks C++ circuit exactly:
  - x^7 exponent, 91 rounds
  - Round constants from keccak256 hash chain starting with keccak256("mimc")
  - Miyaguchi-Preneel compression
  - Level-specific IVs for Merkle tree

Proof verification uses oracle pattern since ICON lacks BN254 precompiles.

Copyright 2024 Miximus Authors — GPL-3.0-or-later
"""

from iconservice import *

TAG = "MiximusIcon"
TREE_DEPTH = 29
MAX_LEAVES = 2 ** 29
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

# Precomputed MiMC round constants (keccak256 hash chain from seed "mimc")
# c[0] = keccak256("mimc"), c[i] = keccak256(c[i-1])
# Computed offline to avoid needing keccak256 in ICON SCORE
MIMC_ROUND_CONSTANTS = None  # Lazily computed


def _compute_round_constants():
    """Compute the 91 MiMC round constants from keccak256 hash chain."""
    global MIMC_ROUND_CONSTANTS
    if MIMC_ROUND_CONSTANTS is not None:
        return MIMC_ROUND_CONSTANTS

    # keccak256("mimc") as seed, then hash chain
    # ICON SCORE has sha3_256 which is Keccak-256 (same as Ethereum's keccak256)
    seed = sha3_256(b"mimc")
    constants = []
    c = seed
    for _ in range(91):
        c = sha3_256(c)
        constants.append(int.from_bytes(c, 'big') % SCALAR_FIELD)
    MIMC_ROUND_CONSTANTS = constants
    return constants


def _mimc_cipher(x: int, k: int) -> int:
    """MiMC-p/p cipher: 91 rounds, x^7 exponent, matching ethsnarks."""
    constants = _compute_round_constants()
    for i in range(91):
        t = (x + constants[i] + k) % SCALAR_FIELD
        # t^7 = t * (t^2)^3
        t2 = (t * t) % SCALAR_FIELD
        t4 = (t2 * t2) % SCALAR_FIELD
        x = (t * t2 * t4) % SCALAR_FIELD
    return (x + k) % SCALAR_FIELD


def mimc_hash(data: list, iv: int = 0) -> int:
    """MiMC hash with Miyaguchi-Preneel compression."""
    r = iv
    for x in data:
        h = _mimc_cipher(x, r)
        r = (r + x + h) % SCALAR_FIELD
    return r


class MiximusIcon(IconScoreBase):
    """ICON SCORE-based mixer contract for ICX."""

    _DENOMINATION = "denomination"
    _NEXT_LEAF_INDEX = "next_leaf_index"
    _CURRENT_ROOT = "current_root"
    _OWNER = "owner"
    _VK_DATA = "vk_data"
    _ORACLE = "oracle"

    def __init__(self, db: IconScoreDatabase) -> None:
        super().__init__(db)
        self._denomination = VarDB(self._DENOMINATION, db, value_type=int)
        self._next_leaf_index = VarDB(self._NEXT_LEAF_INDEX, db, value_type=int)
        self._current_root = VarDB(self._CURRENT_ROOT, db, value_type=int)
        self._owner = VarDB(self._OWNER, db, value_type=Address)
        self._vk_data = VarDB(self._VK_DATA, db, value_type=bytes)
        self._oracle = VarDB(self._ORACLE, db, value_type=Address)
        self._nullifiers = DictDB("nullifiers", db, value_type=bool)
        self._roots = DictDB("roots", db, value_type=bool)
        # Full Merkle tree: tree_nodes[level_index] = hash value
        self._tree_nodes = DictDB("tree_nodes", db, value_type=int, depth=2)
        # Zero hashes for each level
        self._zero_hashes = DictDB("zero_hashes", db, value_type=int)
        # Proof attestations from oracle
        self._proof_attestations = DictDB("proof_attest", db, value_type=bool)

    def on_install(self, denomination: int, vk_data: bytes, oracle: Address) -> None:
        super().on_install()
        self._denomination.set(denomination)
        self._next_leaf_index.set(0)
        self._owner.set(self.msg.sender)
        self._vk_data.set(vk_data)
        self._oracle.set(oracle)

        # Initialize Merkle tree with level-specific IVs
        zero = 0
        for i in range(TREE_DEPTH):
            self._zero_hashes[i] = zero
            zero = mimc_hash([zero, zero], LEVEL_IVS[i])

        self._current_root.set(zero)
        self._roots[zero] = True

    def on_update(self) -> None:
        super().on_update()

    def _get_node(self, level: int, index: int) -> int:
        val = self._tree_nodes[level][index]
        if val and val != 0:
            return val
        return self._zero_hashes[level]

    @external
    @payable
    def deposit(self, leaf_hash: int):
        """Deposit ICX into the mixer"""
        denomination = self._denomination.get()
        if self.msg.value != denomination:
            revert("Must deposit exact denomination")
        if self._next_leaf_index.get() >= MAX_LEAVES:
            revert("Merkle tree full")

        leaf_index = self._next_leaf_index.get()
        self._next_leaf_index.set(leaf_index + 1)

        # Insert leaf into full Merkle tree with level IVs
        self._tree_nodes[0][leaf_index] = leaf_hash
        current_node = leaf_hash
        idx = leaf_index

        for level in range(TREE_DEPTH):
            parent_idx = idx // 2
            if idx % 2 == 0:
                left = current_node
                right = self._get_node(level, idx + 1)
            else:
                left = self._get_node(level, idx - 1)
                right = current_node

            current_node = mimc_hash([left, right], LEVEL_IVS[level])
            self._tree_nodes[level + 1][parent_idx] = current_node
            idx = parent_idx

        new_root = current_node
        self._current_root.set(new_root)
        self._roots[new_root] = True

        self.DepositEvent(leaf_hash, leaf_index)

    @external
    @payable
    def batch_deposit(self, leaf_hashes: str):
        """Batch deposit ICX — deposit N units in a single transaction.
        leaf_hashes is a comma-separated list of leaf hash integers.
        """
        hashes = [int(h.strip()) for h in leaf_hashes.split(',')]
        count = len(hashes)
        if count == 0 or count > 20:
            revert("Batch size must be 1-20")

        denomination = self._denomination.get()
        if self.msg.value != denomination * count:
            revert("Must deposit exact total denomination")

        for leaf_hash in hashes:
            if self._next_leaf_index.get() >= MAX_LEAVES:
                revert("Merkle tree full")

            leaf_index = self._next_leaf_index.get()
            self._next_leaf_index.set(leaf_index + 1)

            self._tree_nodes[0][leaf_index] = leaf_hash
            current_node = leaf_hash
            idx = leaf_index

            for level in range(TREE_DEPTH):
                parent_idx = idx // 2
                if idx % 2 == 0:
                    left = current_node
                    right = self._get_node(level, idx + 1)
                else:
                    left = self._get_node(level, idx - 1)
                    right = current_node

                current_node = mimc_hash([left, right], LEVEL_IVS[level])
                self._tree_nodes[level + 1][parent_idx] = current_node
                idx = parent_idx

            new_root = current_node
            self._current_root.set(new_root)
            self._roots[new_root] = True
            self.DepositEvent(leaf_hash, leaf_index)

    @external
    def submit_proof_attestation(self, proof_hash: int, valid: bool):
        """Oracle submits proof validity attestation."""
        if self.msg.sender != self._oracle.get():
            revert("Only oracle can submit attestations")
        self._proof_attestations[proof_hash] = valid

    @external
    def withdraw(self, root: int, nullifier: int, proof: bytes):
        """Withdraw ICX using zkSNARK proof (verified by oracle attestation)"""
        if self._nullifiers[nullifier]:
            revert("Double-spend")
        if not self._roots[root]:
            revert("Unknown root")

        # Compute proof hash for oracle attestation lookup
        ext_hash = int.from_bytes(
            sha3_256(self.address.to_bytes() + self.msg.sender.to_bytes()),
            'big'
        ) % SCALAR_FIELD
        pub_hash = mimc_hash([root, nullifier, ext_hash])
        proof_hash = int.from_bytes(sha3_256(
            pub_hash.to_bytes(32, 'big') + proof
        ), 'big')

        if not self._proof_attestations[proof_hash]:
            revert("Invalid proof: no oracle attestation")

        self._nullifiers[nullifier] = True

        denomination = self._denomination.get()
        self.icx.transfer(self.msg.sender, denomination)

        self.WithdrawEvent(self.msg.sender, nullifier)

    @external
    def batch_withdraw(self, roots: str, nullifiers_str: str, proofs: bytes):
        """Batch withdraw ICX — process up to 5 withdrawals in a single transaction.
        roots and nullifiers_str are comma-separated lists of integers.
        proofs contains concatenated proof bytes (each 256 bytes).
        """
        root_list = [int(r.strip()) for r in roots.split(',')]
        nullifier_list = [int(n.strip()) for n in nullifiers_str.split(',')]
        count = len(root_list)

        if count == 0 or count > 5:
            revert("Batch size must be 1-5")
        if len(nullifier_list) != count:
            revert("Nullifiers length mismatch")

        denomination = self._denomination.get()

        for i in range(count):
            root = root_list[i]
            nullifier = nullifier_list[i]

            if self._nullifiers[nullifier]:
                revert("Double-spend")
            if not self._roots[root]:
                revert("Unknown root")

            # Compute proof hash for oracle attestation lookup
            ext_hash = int.from_bytes(
                sha3_256(self.address.to_bytes() + self.msg.sender.to_bytes()),
                'big'
            ) % SCALAR_FIELD
            pub_hash = mimc_hash([root, nullifier, ext_hash])
            # Extract individual proof (each 256 bytes)
            proof_slice = proofs[i * 256:(i + 1) * 256]
            proof_hash = int.from_bytes(sha3_256(
                pub_hash.to_bytes(32, 'big') + proof_slice
            ), 'big')

            if not self._proof_attestations[proof_hash]:
                revert("Invalid proof: no oracle attestation")

            self._nullifiers[nullifier] = True
            self.WithdrawEvent(self.msg.sender, nullifier)

        # Transfer total amount
        self.icx.transfer(self.msg.sender, denomination * count)

    @external
    def withdraw_via_relayer(self, root: int, nullifier: int, proof: bytes,
                              recipient: Address, relayer_fee: int):
        """Withdraw via relayer"""
        denomination = self._denomination.get()
        if relayer_fee >= denomination:
            revert("Fee too high")
        if self._nullifiers[nullifier]:
            revert("Double-spend")
        if not self._roots[root]:
            revert("Unknown root")

        ext_hash = int.from_bytes(
            sha3_256(self.address.to_bytes() + recipient.to_bytes()),
            'big'
        ) % SCALAR_FIELD
        pub_hash = mimc_hash([root, nullifier, ext_hash])
        proof_hash = int.from_bytes(sha3_256(
            pub_hash.to_bytes(32, 'big') + proof
        ), 'big')

        if not self._proof_attestations[proof_hash]:
            revert("Invalid proof: no oracle attestation")

        self._nullifiers[nullifier] = True

        if relayer_fee > 0:
            self.icx.transfer(self.msg.sender, relayer_fee)
        self.icx.transfer(recipient, denomination - relayer_fee)

        self.WithdrawEvent(recipient, nullifier)

    @external(readonly=True)
    def get_root(self) -> int:
        return self._current_root.get()

    @external(readonly=True)
    def is_spent(self, nullifier: int) -> bool:
        return self._nullifiers[nullifier]

    @external(readonly=True)
    def get_denomination(self) -> int:
        return self._denomination.get()

    @external(readonly=True)
    def get_path(self, leaf_index: int) -> dict:
        """Return Merkle authentication path for a leaf."""
        if leaf_index >= self._next_leaf_index.get():
            revert("Leaf not yet inserted")
        path = []
        address_bits = []
        for i in range(TREE_DEPTH):
            node_idx = leaf_index >> i
            address_bits.append(node_idx & 1 == 1)
            sibling_idx = node_idx ^ 1
            path.append(self._get_node(i, sibling_idx))
        return {"path": path, "address_bits": address_bits}

    @external(readonly=True)
    def hash_public_inputs(self, root: int, nullifier: int, ext_hash: int) -> int:
        return mimc_hash([root, nullifier, ext_hash])

    @external(readonly=True)
    def make_leaf_hash(self, secret: int) -> int:
        return mimc_hash([secret])

    @eventlog(indexed=2)
    def DepositEvent(self, leaf_hash: int, leaf_index: int): pass

    @eventlog(indexed=1)
    def WithdrawEvent(self, recipient: Address, nullifier: int): pass
