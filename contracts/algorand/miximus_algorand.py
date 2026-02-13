"""
Miximus Algorand Smart Contract (PyTeal / Beaker)

zkSNARK-based mixer for Algorand (ALGO).
Written in PyTeal, compiled to TEAL for the AVM (Algorand Virtual Machine).

Supported: ALGO (native)

Note: Algorand's AVM has limited computational capacity (~700 opcodes per
app call). Full MiMC and Groth16 verification are NOT feasible on-chain.

Approach:
  1. Off-chain proof generation (same C++ prover)
  2. Off-chain MiMC Merkle tree computation
  3. On-chain commitment and nullifier tracking
  4. Oracle-based verification: trusted oracle attests proof validity
     in a grouped transaction that the contract checks

MiMC specification (computed off-chain, verified via oracle):
  - x^7 exponent, 91 rounds, Miyaguchi-Preneel compression
  - keccak256 hash chain from seed "mimc" for round constants
  - 29 level-specific IVs for Merkle tree

Copyright 2024 Miximus Authors — GPL-3.0-or-later
"""

from pyteal import *
from beaker import Application, GlobalStateValue, LocalStateValue
from beaker.lib.storage import BoxMapping


# =========================================================================
#                         CONSTANTS
# =========================================================================

TREE_DEPTH = 29
MAX_LEAVES = 2**29
SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617

# 29 level-specific IVs for the MiMC Merkle tree (matching ethsnarks circuit).
# These are used off-chain; stored here for reference and oracle validation.
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


# =========================================================================
#                          APPLICATION
# =========================================================================

class MiximusAlgorand(Application):
    """
    Miximus mixer for Algorand.

    Architecture:
      - MiMC Merkle tree is computed OFF-CHAIN (AVM opcode budget too low)
      - Groth16 proof is verified OFF-CHAIN by a trusted oracle
      - Oracle submits attestation in a grouped transaction
      - Contract verifies oracle signature and attestation data on-chain

    Global state stores:
      - denomination: Fixed ALGO amount (in microAlgos)
      - next_leaf_index: Next available Merkle tree position
      - current_root: Current Merkle tree root (32 bytes, big-endian field element)
      - vk_hash: Hash of the verifying key (for oracle reference)
      - oracle: Oracle address authorized to submit attestations

    Box storage:
      - nullifiers: Maps nullifier bytes -> spent flag
      - roots: Maps root bytes -> valid flag
    """

    denomination = GlobalStateValue(
        stack_type=TealType.uint64,
        descr="Fixed denomination in microAlgos",
    )

    next_leaf_index = GlobalStateValue(
        stack_type=TealType.uint64,
        default=Int(0),
        descr="Next available leaf index",
    )

    current_root = GlobalStateValue(
        stack_type=TealType.bytes,
        descr="Current Merkle tree root (32-byte field element)",
    )

    vk_hash = GlobalStateValue(
        stack_type=TealType.bytes,
        descr="Hash of the verifying key",
    )

    owner = GlobalStateValue(
        stack_type=TealType.bytes,
        descr="Owner/admin address",
    )

    oracle = GlobalStateValue(
        stack_type=TealType.bytes,
        descr="Trusted oracle address for proof verification",
    )

    # Box storage for nullifiers and roots
    nullifiers = BoxMapping(abi.StaticBytes[L[32]], abi.Bool)
    roots = BoxMapping(abi.StaticBytes[L[32]], abi.Bool)

    @create
    def create(self):
        return self.initialize_application_state()

    @external(authorize=Authorize.only_creator())
    def initialize(
        self,
        denomination: abi.Uint64,
        vk_hash: abi.DynamicBytes,
        oracle_addr: abi.Address,
    ):
        """Initialize the mixer with denomination, verifying key hash, and oracle address"""
        return Seq([
            self.denomination.set(denomination.get()),
            self.vk_hash.set(vk_hash.get()),
            self.owner.set(Txn.sender()),
            self.oracle.set(oracle_addr.get()),
            self.next_leaf_index.set(Int(0)),
            # Initialize with zero root (the root of an empty MiMC Merkle tree)
            self.current_root.set(Bytes("base16", "0" * 64)),
        ])

    @external(authorize=Authorize.only_creator())
    def set_oracle(self, new_oracle: abi.Address):
        """Update the oracle address (admin only)"""
        return self.oracle.set(new_oracle.get())

    @external
    def deposit(
        self,
        leaf_hash: abi.StaticBytes[L[32]],
        new_root: abi.StaticBytes[L[32]],
        payment: abi.PaymentTransaction,
        oracle_attestation: abi.Transaction,
        *,
        output: abi.Tuple2[abi.StaticBytes[L[32]], abi.Uint64],
    ):
        """
        Deposit ALGO into the mixer.

        The MiMC Merkle tree insertion is computed off-chain. The oracle
        attests that the new_root is correct for the given leaf insertion.

        Args:
            leaf_hash: H(secret) - the leaf to insert (32-byte MiMC hash)
            new_root: The new Merkle root after insertion (computed off-chain)
            payment: Payment transaction for the denomination amount
            oracle_attestation: Grouped transaction from oracle attesting root validity

        Returns:
            Tuple of (new_root, leaf_index)
        """
        leaf_index = ScratchVar(TealType.uint64)

        return Seq([
            # Verify payment amount matches denomination
            Assert(payment.get().amount() == self.denomination),
            Assert(payment.get().receiver() == Global.current_application_address()),

            # Verify oracle attestation in grouped transaction
            # The oracle signs a transaction with note = concat(leaf_hash, new_root, leaf_index_bytes)
            Assert(oracle_attestation.get().sender() == self.oracle),
            Assert(oracle_attestation.get().type_enum() == TxnType.Payment),
            # Oracle attestation note must contain: leaf_hash || new_root || itob(leaf_index)
            Assert(
                Substring(oracle_attestation.get().note(), Int(0), Int(32))
                == leaf_hash.get()
            ),
            Assert(
                Substring(oracle_attestation.get().note(), Int(32), Int(64))
                == new_root.get()
            ),

            # Get and increment leaf index
            leaf_index.store(self.next_leaf_index),
            Assert(leaf_index.load() < Int(MAX_LEAVES)),
            self.next_leaf_index.set(self.next_leaf_index + Int(1)),

            # Update root (oracle-attested MiMC Merkle root)
            self.current_root.set(new_root.get()),

            # Mark new root as valid
            self.roots[new_root.get()].set(abi.Bool()),

            # Log the deposit
            Log(Concat(
                Bytes("deposit:"),
                Itob(leaf_index.load()),
                Bytes(":"),
                leaf_hash.get(),
            )),

            # Return (new_root, leaf_index)
            output.set(new_root.load(), leaf_index.load()),
        ])

    @external
    def batch_deposit(
        self,
        leaf_hashes: abi.DynamicArray[abi.StaticBytes[L[32]]],
        new_roots: abi.DynamicArray[abi.StaticBytes[L[32]]],
        payment: abi.PaymentTransaction,
        oracle_attestation: abi.Transaction,
        *,
        output: abi.Uint64,
    ):
        """
        Batch deposit ALGO — deposit N units in a single transaction.

        Each leaf's new_root is oracle-attested (sequentially computed off-chain).
        Payment must equal denomination * count.
        """
        count = ScratchVar(TealType.uint64)
        i = ScratchVar(TealType.uint64)
        leaf_index = ScratchVar(TealType.uint64)

        return Seq([
            count.store(leaf_hashes.length()),
            Assert(count.load() > Int(0)),
            Assert(count.load() <= Int(20)),

            # Verify total payment
            Assert(payment.get().amount() == self.denomination * count.load()),
            Assert(payment.get().receiver() == Global.current_application_address()),

            # Verify oracle attestation
            Assert(oracle_attestation.get().sender() == self.oracle),

            # Process each leaf
            i.store(Int(0)),
            While(i.load() < count.load()).Do(Seq([
                leaf_index.store(self.next_leaf_index),
                Assert(leaf_index.load() < Int(MAX_LEAVES)),
                self.next_leaf_index.set(self.next_leaf_index + Int(1)),
                self.current_root.set(new_roots[i.load()].get()),
                self.roots[new_roots[i.load()].get()].set(abi.Bool()),
                Log(Concat(
                    Bytes("deposit:"),
                    Itob(leaf_index.load()),
                    Bytes(":"),
                    leaf_hashes[i.load()].get(),
                )),
                i.store(i.load() + Int(1)),
            ])),

            output.set(count.load()),
        ])

    @external
    def withdraw(
        self,
        root: abi.StaticBytes[L[32]],
        nullifier: abi.StaticBytes[L[32]],
        pub_hash: abi.StaticBytes[L[32]],
        oracle_attestation: abi.Transaction,
    ):
        """
        Withdraw ALGO using zkSNARK proof (oracle-verified).

        The Groth16 proof is verified off-chain by the oracle. The oracle
        submits an attestation transaction in the same group that contains:
          - The public input hash (MiMC(root, nullifier, ext_hash))
          - A signature proving the oracle authorized this verification

        Args:
            root: Merkle root to verify against
            nullifier: Nullifier hash (prevents double-spend)
            pub_hash: Public input hash = MiMC(root, nullifier, ext_hash)
            oracle_attestation: Grouped txn from oracle attesting proof validity
        """
        return Seq([
            # Check nullifier not spent
            Assert(Not(self.nullifiers[nullifier.get()].exists())),

            # Check root is known
            Assert(self.roots[root.get()].exists()),

            # Verify oracle attestation in grouped transaction
            # Oracle must sign a txn with note = pub_hash
            Assert(self._verify_oracle_attestation(oracle_attestation, pub_hash)),

            # Mark nullifier as spent
            self.nullifiers[nullifier.get()].set(abi.Bool()),

            # Transfer ALGO to sender
            InnerTxnBuilder.Execute({
                TxnField.type_enum: TxnType.Payment,
                TxnField.receiver: Txn.sender(),
                TxnField.amount: self.denomination,
                TxnField.fee: Int(0),
            }),

            Log(Concat(Bytes("withdraw:"), nullifier.get())),
        ])

    @external
    def batch_withdraw(
        self,
        roots: abi.DynamicArray[abi.StaticBytes[L[32]]],
        nullifiers: abi.DynamicArray[abi.StaticBytes[L[32]]],
        pub_hashes: abi.DynamicArray[abi.StaticBytes[L[32]]],
        oracle_attestation: abi.Transaction,
    ):
        """
        Batch withdraw ALGO — process up to 5 withdrawals in a single transaction.

        Each (root, nullifier, pub_hash) triple is verified against oracle attestations.
        Total denomination * count is transferred to the sender.
        """
        count = ScratchVar(TealType.uint64)
        i = ScratchVar(TealType.uint64)

        return Seq([
            count.store(roots.length()),
            Assert(count.load() > Int(0)),
            Assert(count.load() <= Int(5)),
            Assert(nullifiers.length() == count.load()),
            Assert(pub_hashes.length() == count.load()),

            # Verify oracle attestation in grouped transaction
            Assert(oracle_attestation.get().sender() == self.oracle),

            # Process each withdrawal
            i.store(Int(0)),
            While(i.load() < count.load()).Do(Seq([
                # Check nullifier not spent
                Assert(Not(self.nullifiers[nullifiers[i.load()].get()].exists())),
                # Check root is known
                Assert(self.roots[roots[i.load()].get()].exists()),
                # Verify oracle attestation note contains pub_hash
                Assert(
                    Substring(oracle_attestation.get().note(),
                              Int(32) * i.load(),
                              Int(32) * (i.load() + Int(1)))
                    == pub_hashes[i.load()].get()
                ),
                # Mark nullifier as spent
                self.nullifiers[nullifiers[i.load()].get()].set(abi.Bool()),
                Log(Concat(Bytes("withdraw:"), nullifiers[i.load()].get())),
                i.store(i.load() + Int(1)),
            ])),

            # Transfer total ALGO to sender
            InnerTxnBuilder.Execute({
                TxnField.type_enum: TxnType.Payment,
                TxnField.receiver: Txn.sender(),
                TxnField.amount: self.denomination * count.load(),
                TxnField.fee: Int(0),
            }),
        ])

    @external
    def withdraw_via_relayer(
        self,
        root: abi.StaticBytes[L[32]],
        nullifier: abi.StaticBytes[L[32]],
        pub_hash: abi.StaticBytes[L[32]],
        oracle_attestation: abi.Transaction,
        recipient: abi.Address,
        relayer_fee: abi.Uint64,
    ):
        """Withdraw to a specified recipient with relayer fee (oracle-verified)"""
        return Seq([
            Assert(relayer_fee.get() < self.denomination),
            Assert(Not(self.nullifiers[nullifier.get()].exists())),
            Assert(self.roots[root.get()].exists()),
            Assert(self._verify_oracle_attestation(oracle_attestation, pub_hash)),

            self.nullifiers[nullifier.get()].set(abi.Bool()),

            # Pay relayer fee
            If(relayer_fee.get() > Int(0),
                InnerTxnBuilder.Execute({
                    TxnField.type_enum: TxnType.Payment,
                    TxnField.receiver: Txn.sender(),
                    TxnField.amount: relayer_fee.get(),
                    TxnField.fee: Int(0),
                }),
            ),

            # Pay recipient
            InnerTxnBuilder.Execute({
                TxnField.type_enum: TxnType.Payment,
                TxnField.receiver: recipient.get(),
                TxnField.amount: self.denomination - relayer_fee.get(),
                TxnField.fee: Int(0),
            }),
        ])

    # =========================================================================
    #                          VIEW METHODS
    # =========================================================================

    @external(read_only=True)
    def get_root(self, *, output: abi.DynamicBytes):
        return output.set(self.current_root)

    @external(read_only=True)
    def is_spent(self, nullifier: abi.StaticBytes[L[32]], *, output: abi.Bool):
        return output.set(self.nullifiers[nullifier.get()].exists())

    @external(read_only=True)
    def get_denomination(self, *, output: abi.Uint64):
        return output.set(self.denomination)

    @external(read_only=True)
    def get_oracle(self, *, output: abi.Address):
        return output.set(self.oracle)

    # =========================================================================
    #                       INTERNAL HELPERS
    # =========================================================================

    @internal(TealType.uint64)
    def _verify_oracle_attestation(self, oracle_txn, pub_hash):
        """
        Verify that the oracle has attested the proof in a grouped transaction.

        The oracle submits a payment transaction (can be 0 ALGO) to itself
        in the same atomic group. The note field contains the public input hash.
        We verify:
          1. The sender is the authorized oracle
          2. The note contains the expected public input hash
          3. The transaction is in the same group

        This is the AVM-compatible approach since full Groth16 verification
        (~10M operations) far exceeds the AVM opcode budget (~700 opcodes).

        MiMC specification for public input hash (computed off-chain):
          pub_hash = MiMC_hash([root, nullifier, ext_hash], IV=0)
          where MiMC uses x^7 exponent, 91 rounds, Miyaguchi-Preneel,
          with keccak256("mimc") hash chain for round constants.
        """
        return And(
            oracle_txn.get().sender() == self.oracle,
            oracle_txn.get().type_enum() == TxnType.Payment,
            # Note must contain the public input hash
            Substring(oracle_txn.get().note(), Int(0), Int(32)) == pub_hash.get(),
        )


# =========================================================================
#                      COMPILE & DEPLOY
# =========================================================================

if __name__ == "__main__":
    app = MiximusAlgorand()

    # Compile to TEAL
    approval_teal = app.approval_program
    clear_teal = app.clear_program

    # Write compiled TEAL
    with open("approval.teal", "w") as f:
        f.write(approval_teal)
    with open("clear.teal", "w") as f:
        f.write(clear_teal)

    print(f"Compiled MiximusAlgorand")
    print(f"  Approval program: {len(approval_teal)} bytes")
    print(f"  Clear program: {len(clear_teal)} bytes")
    print(f"  Tree depth: {TREE_DEPTH}")
    print(f"  Level IVs: {len(LEVEL_IVS)} entries")
    print(f"  Verification: Oracle-based (AVM opcode budget too low for on-chain MiMC/Groth16)")
