"""
Base chain adapter — abstract interface for all blockchain implementations.

Every chain adapter implements this interface, ensuring a uniform API
regardless of the underlying blockchain technology.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, List


class ChainType(Enum):
    """Categories of blockchain platforms"""
    EVM = "evm"           # Ethereum, BSC, Polygon, Avalanche, Arbitrum, Base, etc.
    TVM = "tvm"           # Tron
    SVM = "svm"           # Solana
    CARDANO = "cardano"
    COSMOS = "cosmos"     # Cosmos, Terra
    SUBSTRATE = "substrate"  # Polkadot, Moonbeam
    NEAR = "near"
    ALGORAND = "algorand"
    TEZOS = "tezos"
    TON = "ton"
    STELLAR = "stellar"
    EOSIO = "eosio"       # EOS
    NEO = "neo"
    WAVES = "waves"
    ICON = "icon"
    UTXO = "utxo"         # Bitcoin, Litecoin, Dogecoin, etc.
    XRPL = "xrpl"         # Ripple
    NEM = "nem"
    ONTOLOGY = "ontology"


@dataclass
class DepositResult:
    """Result of a deposit operation"""
    success: bool
    tx_hash: str
    leaf_index: int
    new_root: str
    chain: str
    asset: str
    amount: str
    error: Optional[str] = None


@dataclass
class BatchDepositResult:
    """Result of a batch deposit operation (N leaves in one transaction)"""
    success: bool
    tx_hash: str
    leaf_indices: List[int]
    new_root: str
    chain: str
    asset: str
    total_amount: str
    count: int
    error: Optional[str] = None


@dataclass
class WithdrawResult:
    """Result of a withdrawal operation"""
    success: bool
    tx_hash: str
    nullifier: str
    recipient: str
    chain: str
    asset: str
    amount: str
    error: Optional[str] = None


@dataclass
class BatchWithdrawResult:
    """Result of a batch withdrawal operation (N withdrawals in one transaction)"""
    success: bool
    tx_hash: str
    nullifiers: List[str]
    recipient: str
    chain: str
    asset: str
    total_amount: str
    count: int
    error: Optional[str] = None


@dataclass
class ProofData:
    """zkSNARK proof data (chain-agnostic)"""
    root: int
    nullifier: int
    proof_json: str
    proof_points: List[int]  # [A.x, A.y, B.x1, B.y1, B.x2, B.y2, C.x, C.y]
    external_hash: int


class ChainAdapter(ABC):
    """
    Abstract base class for all chain adapters.

    Each blockchain platform implements this interface to provide
    a uniform deposit/withdraw API. The zkSNARK circuit and proof
    generation are shared across all chains (via the C++ native library).
    """

    def __init__(self, chain_id: str, chain_type: ChainType, rpc_url: str,
                 native_symbol: str, denomination: int):
        self.chain_id = chain_id
        self.chain_type = chain_type
        self.rpc_url = rpc_url
        self.native_symbol = native_symbol
        self.denomination = denomination

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the blockchain node"""
        pass

    @abstractmethod
    def get_root(self) -> int:
        """Get current Merkle tree root from the on-chain contract"""
        pass

    @abstractmethod
    def get_path(self, leaf_index: int) -> Tuple[List[int], List[bool]]:
        """Get Merkle authentication path for a leaf"""
        pass

    @abstractmethod
    def deposit(self, leaf_hash: int, private_key: str) -> DepositResult:
        """
        Deposit funds into the mixer.

        Args:
            leaf_hash: H(secret) — the commitment to insert
            private_key: Wallet private key for signing

        Returns:
            DepositResult with transaction details
        """
        pass

    @abstractmethod
    def batch_deposit(self, leaf_hashes: List[int], private_key: str) -> BatchDepositResult:
        """
        Batch deposit — insert multiple leaves in a single transaction.

        Args:
            leaf_hashes: List of H(secret) commitments to insert
            private_key: Wallet private key for signing

        Returns:
            BatchDepositResult with transaction details and all leaf indices
        """
        pass

    @abstractmethod
    def withdraw(self, proof: ProofData, recipient: str,
                 private_key: str) -> WithdrawResult:
        """
        Withdraw funds using a zkSNARK proof.

        Args:
            proof: The zkSNARK proof data
            recipient: Destination address
            private_key: Wallet private key for signing

        Returns:
            WithdrawResult with transaction details
        """
        pass

    @abstractmethod
    def batch_withdraw(self, proofs: List[ProofData], recipient: str,
                       private_key: str) -> BatchWithdrawResult:
        """
        Batch withdraw — withdraw multiple notes in a single transaction.

        Args:
            proofs: List of zkSNARK proof data (one per note)
            recipient: Destination address for all withdrawals
            private_key: Wallet private key for signing

        Returns:
            BatchWithdrawResult with transaction details and all nullifiers
        """
        pass

    @abstractmethod
    def is_spent(self, nullifier: int) -> bool:
        """Check if a nullifier has already been spent"""
        pass

    @abstractmethod
    def get_balance(self) -> int:
        """Get contract balance (total deposited funds)"""
        pass

    def get_ext_hash(self, contract_address: str, sender_address: str) -> int:
        """
        Compute external hash binding the proof to a specific contract/sender.
        This is the same computation across all chains.
        """
        import hashlib
        data = contract_address.encode() + sender_address.encode()
        h = int.from_bytes(hashlib.sha256(data).digest(), 'big')
        SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617
        return h % SCALAR_FIELD

    def __repr__(self):
        return f"<{self.__class__.__name__} chain={self.chain_id} symbol={self.native_symbol}>"
