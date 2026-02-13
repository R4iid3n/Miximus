"""
Tron (TVM) Chain Adapter

Handles interaction with MiximusTRC20 and MiximusNativeTron contracts
deployed on Tron mainnet and Tron Nile testnet.

Tron differences from EVM:
  - 21-byte addresses in base58check format (T-prefix)
  - Energy/bandwidth model instead of gas (uses fee_limit in SUN)
  - TRC20 tokens require approve() before deposit (same as ERC20)
  - Contract calls use tronpy library (not web3.py)
  - Transaction results are polled asynchronously (not mined instantly)

Uses the `tronpy` Python library for all Tron interactions.
"""

import json
import logging
import time
from typing import Optional, Tuple, List, Dict, Any

from .base import (
    ChainAdapter, ChainType, DepositResult, BatchDepositResult,
    WithdrawResult, BatchWithdrawResult, ProofData
)

logger = logging.getLogger(__name__)

# Default fee limits in SUN (1 TRX = 1,000,000 SUN)
DEFAULT_FEE_LIMIT = 150_000_000      # 150 TRX — sufficient for most contract calls
APPROVE_FEE_LIMIT = 50_000_000       # 50 TRX — for TRC20 approve()
DEPOSIT_FEE_LIMIT = 200_000_000      # 200 TRX — deposit touches Merkle tree
WITHDRAW_FEE_LIMIT = 300_000_000     # 300 TRX — withdraw runs pairing check
BATCH_FEE_LIMIT_PER_ITEM = 200_000_000  # additional per batch item

# Transaction confirmation polling
TX_POLL_INTERVAL_SECONDS = 3
TX_POLL_MAX_ATTEMPTS = 40  # 40 * 3s = 120s max wait

# Merkle tree depth (must match contract constant)
TREE_DEPTH = 29

# ABI fragments for MiximusTRC20 / MiximusNativeTron contract interaction.
# tronpy uses ABI JSON the same way as web3.py.
MIXER_ABI = [
    {
        "type": "function", "name": "deposit",
        "inputs": [{"name": "_leaf", "type": "uint256"}],
        "outputs": [
            {"name": "newRoot", "type": "uint256"},
            {"name": "leafIndex", "type": "uint256"}
        ],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function", "name": "batchDeposit",
        "inputs": [{"name": "_leaves", "type": "uint256[]"}],
        "outputs": [{"name": "startIndex", "type": "uint256"}],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function", "name": "withdraw",
        "inputs": [
            {"name": "_root", "type": "uint256"},
            {"name": "_nullifier", "type": "uint256"},
            {"name": "_proof", "type": "uint256[8]"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function", "name": "withdrawViaRelayer",
        "inputs": [
            {"name": "_root", "type": "uint256"},
            {"name": "_nullifier", "type": "uint256"},
            {"name": "_proof", "type": "uint256[8]"},
            {"name": "_recipient", "type": "address"},
            {"name": "_relayerFee", "type": "uint256"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function", "name": "batchWithdraw",
        "inputs": [
            {"name": "_roots", "type": "uint256[]"},
            {"name": "_nullifiers", "type": "uint256[]"},
            {"name": "_proofs", "type": "uint256[8][]"}
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function", "name": "getRoot",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "type": "function", "name": "getPath",
        "inputs": [{"name": "_leafIndex", "type": "uint256"}],
        "outputs": [
            {"name": "path", "type": "uint256[29]"},
            {"name": "addressBits", "type": "bool[29]"}
        ],
        "stateMutability": "view"
    },
    {
        "type": "function", "name": "isSpent",
        "inputs": [{"name": "_nullifier", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view"
    },
    {
        "type": "function", "name": "nextLeafIndex",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "type": "function", "name": "denomination",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "type": "function", "name": "getExtHash",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "type": "function", "name": "token",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view"
    },
    {
        "type": "event", "name": "Deposit",
        "inputs": [
            {"name": "leafHash", "type": "uint256", "indexed": True},
            {"name": "leafIndex", "type": "uint256", "indexed": True},
            {"name": "timestamp", "type": "uint256", "indexed": False}
        ]
    },
    {
        "type": "event", "name": "Withdrawal",
        "inputs": [
            {"name": "recipient", "type": "address", "indexed": True},
            {"name": "nullifier", "type": "uint256", "indexed": False},
            {"name": "timestamp", "type": "uint256", "indexed": False}
        ]
    },
]

# Minimal TRC20 ABI for token approve / balanceOf / allowance / transfer
TRC20_TOKEN_ABI = [
    {
        "type": "function", "name": "approve",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function", "name": "balanceOf",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "type": "function", "name": "allowance",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"}
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view"
    },
    {
        "type": "function", "name": "transfer",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function", "name": "decimals",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view"
    },
    {
        "type": "function", "name": "symbol",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view"
    },
    {
        "type": "event", "name": "Transfer",
        "inputs": [
            {"name": "from", "type": "address", "indexed": True},
            {"name": "to", "type": "address", "indexed": True},
            {"name": "value", "type": "uint256", "indexed": False}
        ]
    },
]


class TronAdapter:
    """
    Tron chain adapter for the Miximus custodial mixer service.

    Interacts with MiximusTRC20 and MiximusNativeTron contracts deployed
    on Tron Nile testnet or Tron mainnet via the tronpy library.

    Usage:
        adapter = TronAdapter(
            private_key_hex="abcdef1234...",
            rpc_url="https://nile.trongrid.io",
        )
        root = adapter.get_root("TContractAddress...")
        result = adapter.deposit_to_mixer("TContractAddr...", leaf, denom, "TTokenAddr...")
    """

    # Well-known RPC endpoints
    NILE_TESTNET_RPC = "https://nile.trongrid.io"
    MAINNET_RPC = "https://api.trongrid.io"

    def __init__(self, private_key_hex: str,
                 rpc_url: str = "https://nile.trongrid.io"):
        """
        Initialize the Tron adapter.

        Args:
            private_key_hex: 64-character hex private key (no 0x prefix).
            rpc_url: TronGrid API endpoint. Defaults to Nile testnet.

        Raises:
            ValueError: If private key format is invalid.
            ImportError: If tronpy is not installed.
        """
        # Validate private key format
        clean_key = private_key_hex.strip()
        if clean_key.startswith("0x") or clean_key.startswith("0X"):
            clean_key = clean_key[2:]
        if len(clean_key) != 64:
            raise ValueError(
                f"Private key must be 64 hex characters, got {len(clean_key)}"
            )
        try:
            int(clean_key, 16)
        except ValueError:
            raise ValueError("Private key contains non-hex characters")

        self._private_key_hex = clean_key
        self._rpc_url = rpc_url

        # Lazily initialized tronpy objects
        self._client = None
        self._priv_key = None

    # =========================================================================
    #                        CONNECTION & IDENTITY
    # =========================================================================

    def _ensure_client(self):
        """Lazily initialize the tronpy client and private key object."""
        if self._client is not None:
            return

        try:
            from tronpy import Tron
            from tronpy.keys import PrivateKey
            from tronpy.providers import HTTPProvider
        except ImportError:
            raise ImportError(
                "tronpy is required for Tron adapter. "
                "Install with: pip install tronpy"
            )

        # Determine network from RPC URL
        provider = HTTPProvider(self._rpc_url)

        # tronpy Tron client accepts a provider for custom endpoints
        if "nile" in self._rpc_url.lower():
            self._client = Tron(provider=provider, network="nile")
        elif "shasta" in self._rpc_url.lower():
            self._client = Tron(provider=provider, network="shasta")
        else:
            # Mainnet or custom endpoint
            self._client = Tron(provider=provider, network="mainnet")

        self._priv_key = PrivateKey(bytes.fromhex(self._private_key_hex))
        logger.info(
            "Tron client initialized: rpc=%s address=%s",
            self._rpc_url, self.get_address()
        )

    def get_address(self) -> str:
        """
        Return the Tron base58check address derived from the private key.

        Returns:
            Base58check-encoded Tron address (starts with 'T' on mainnet/testnet).
        """
        self._ensure_client()
        return self._priv_key.public_key.to_base58check_address()

    # =========================================================================
    #                       CONTRACT HELPER METHODS
    # =========================================================================

    def _get_contract(self, contract_address: str, abi: list = None):
        """
        Load a tronpy contract object.

        Args:
            contract_address: Base58check Tron address of the contract.
            abi: ABI list. If None, fetches from chain (requires verified source).

        Returns:
            tronpy Contract object.
        """
        self._ensure_client()
        contract = self._client.get_contract(contract_address)
        if abi is not None:
            contract.abi = abi
        return contract

    def _wait_for_tx(self, tx_hash: str, max_attempts: int = TX_POLL_MAX_ATTEMPTS
                     ) -> Dict[str, Any]:
        """
        Poll for transaction confirmation on Tron.

        Tron transactions are not confirmed instantly. We poll the
        transaction info endpoint until we get a receipt or timeout.

        Args:
            tx_hash: Transaction ID (hex string).
            max_attempts: Maximum poll attempts.

        Returns:
            Transaction info dict from TronGrid.

        Raises:
            TimeoutError: If transaction not confirmed within max_attempts.
            RuntimeError: If transaction reverted.
        """
        self._ensure_client()

        for attempt in range(1, max_attempts + 1):
            try:
                tx_info = self._client.get_transaction_info(tx_hash)
                # tx_info is empty dict {} until the block is confirmed
                if tx_info and tx_info.get("id"):
                    receipt = tx_info.get("receipt", {})
                    result = receipt.get("result", "")

                    # "SUCCESS" or absence of "result" with no revert
                    if result == "REVERT":
                        revert_msg = self._decode_revert_reason(tx_info)
                        raise RuntimeError(
                            f"Transaction reverted: {revert_msg} (txid={tx_hash})"
                        )

                    if result == "OUT_OF_ENERGY":
                        raise RuntimeError(
                            f"Transaction ran out of energy. Increase fee_limit. "
                            f"(txid={tx_hash})"
                        )

                    # SUCCESS or empty result means OK
                    logger.debug(
                        "TX confirmed: %s (attempt %d/%d)",
                        tx_hash, attempt, max_attempts
                    )
                    return tx_info

            except Exception as e:
                # get_transaction_info may raise if TX not yet indexed
                if attempt == max_attempts:
                    raise
                if isinstance(e, (RuntimeError,)):
                    raise

            time.sleep(TX_POLL_INTERVAL_SECONDS)

        raise TimeoutError(
            f"Transaction {tx_hash} not confirmed after "
            f"{max_attempts * TX_POLL_INTERVAL_SECONDS}s"
        )

    @staticmethod
    def _decode_revert_reason(tx_info: dict) -> str:
        """
        Attempt to decode the revert reason from a Tron transaction info.

        Args:
            tx_info: Transaction info dict from TronGrid.

        Returns:
            Human-readable revert reason, or raw hex if decoding fails.
        """
        # contractResult contains the return data (hex-encoded)
        contract_result = tx_info.get("contractResult", [])
        if not contract_result or not contract_result[0]:
            return "unknown reason"

        raw_hex = contract_result[0]
        try:
            # Standard Solidity revert: first 4 bytes = Error(string) selector
            # 08c379a0 + offset + length + string
            if raw_hex.startswith("08c379a0"):
                data = bytes.fromhex(raw_hex[8:])
                # offset is at bytes 0-31 (always 32)
                str_len = int.from_bytes(data[32:64], "big")
                reason = data[64:64 + str_len].decode("utf-8", errors="replace")
                return reason
        except Exception:
            pass

        return f"0x{raw_hex[:128]}..." if len(raw_hex) > 128 else f"0x{raw_hex}"

    # =========================================================================
    #                          PAYMENT VERIFICATION
    # =========================================================================

    def verify_payment(self, tx_hash: str, expected_amount: int,
                       token_address: str) -> Dict[str, Any]:
        """
        Verify that a TRC20 transfer transaction transferred the expected
        amount to the service wallet.

        This is used by the custodial service to verify that a user has
        paid the required deposit amount before the service deposits into
        the mixer contract on their behalf.

        Args:
            tx_hash: Transaction hash to verify.
            expected_amount: Expected token amount in smallest unit (e.g., SUN for USDT).
            token_address: TRC20 token contract address (base58check).

        Returns:
            Dict with keys:
                - verified (bool): Whether payment is valid.
                - sender (str): Sender address.
                - amount (int): Actual amount transferred.
                - error (str or None): Error message if verification failed.
        """
        self._ensure_client()
        my_address = self.get_address()

        try:
            # Wait for transaction to be confirmed
            tx_info = self._wait_for_tx(tx_hash)
        except (TimeoutError, RuntimeError) as e:
            return {
                "verified": False,
                "sender": "",
                "amount": 0,
                "error": str(e),
            }

        # Get the base transaction to check to/from
        try:
            tx_data = self._client.get_transaction(tx_hash)
        except Exception as e:
            return {
                "verified": False,
                "sender": "",
                "amount": 0,
                "error": f"Failed to fetch transaction: {e}",
            }

        # For TRC20 transfers, look at the log events in tx_info
        # The Transfer event is emitted by the token contract
        logs = tx_info.get("log", [])

        # Transfer event topic: keccak256("Transfer(address,address,uint256)")
        transfer_topic = (
            "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        )

        for log_entry in logs:
            # Check this log is from the expected token contract
            log_address = log_entry.get("address", "")
            # tronpy returns log addresses in hex (without 41 prefix sometimes)
            # Normalize for comparison
            if not self._address_matches(log_address, token_address):
                continue

            topics = log_entry.get("topics", [])
            if len(topics) < 3:
                continue

            if topics[0] != transfer_topic:
                continue

            # topics[1] = from address (zero-padded to 32 bytes, hex)
            # topics[2] = to address (zero-padded to 32 bytes, hex)
            log_to_hex = topics[2][-40:]  # last 20 bytes (EVM-style)
            # Convert our address to hex for comparison
            my_addr_hex = self._base58_to_hex(my_address)

            if log_to_hex.lower() != my_addr_hex.lower():
                continue

            # Data field contains the amount
            data_hex = log_entry.get("data", "")
            if not data_hex:
                continue
            actual_amount = int(data_hex, 16)

            # Extract sender from topics[1]
            sender_hex = topics[1][-40:]
            sender_addr = self._hex_to_base58(sender_hex)

            if actual_amount >= expected_amount:
                return {
                    "verified": True,
                    "sender": sender_addr,
                    "amount": actual_amount,
                    "error": None,
                }
            else:
                return {
                    "verified": False,
                    "sender": sender_addr,
                    "amount": actual_amount,
                    "error": (
                        f"Insufficient amount: expected {expected_amount}, "
                        f"got {actual_amount}"
                    ),
                }

        return {
            "verified": False,
            "sender": "",
            "amount": 0,
            "error": (
                f"No TRC20 Transfer event found to {my_address} "
                f"from token {token_address} in tx {tx_hash}"
            ),
        }

    # =========================================================================
    #                           DEPOSIT
    # =========================================================================

    def deposit_to_mixer(self, mixer_address: str, leaf_hash: int,
                         denomination: int, token_address: str
                         ) -> DepositResult:
        """
        Approve TRC20 token and deposit into the mixer contract.

        Steps:
          1. Check current allowance; approve if insufficient.
          2. Call mixer.deposit(leaf_hash).
          3. Wait for confirmation and parse Deposit event.

        Args:
            mixer_address: Mixer contract address (base58check).
            leaf_hash: Leaf commitment H(secret) to insert into the Merkle tree.
            denomination: Token amount to deposit (in smallest unit).
            token_address: TRC20 token contract address (base58check).

        Returns:
            DepositResult with transaction details.
        """
        self._ensure_client()
        my_address = self.get_address()

        try:
            # Step 1: Ensure sufficient TRC20 allowance
            self._ensure_allowance(token_address, mixer_address, denomination)

            # Step 2: Build and send deposit transaction
            mixer = self._get_contract(mixer_address, abi=MIXER_ABI)
            txn = (
                mixer.functions.deposit(leaf_hash)
                .with_owner(my_address)
                .fee_limit(DEPOSIT_FEE_LIMIT)
                .build()
            )
            signed_txn = txn.sign(self._priv_key)
            result = signed_txn.broadcast()
            tx_hash = result.get("txid", "")

            if not tx_hash:
                return DepositResult(
                    success=False, tx_hash="", leaf_index=-1, new_root="",
                    chain="tron", asset="TRC20",
                    amount=str(denomination),
                    error=f"Broadcast failed: {result}",
                )

            logger.info("Deposit TX broadcast: %s", tx_hash)

            # Step 3: Wait for confirmation
            tx_info = self._wait_for_tx(tx_hash)

            # Parse Deposit event from logs
            leaf_index = self._parse_deposit_event(tx_info)

            # Get updated root
            new_root = str(self.get_root(mixer_address))

            return DepositResult(
                success=True,
                tx_hash=tx_hash,
                leaf_index=leaf_index,
                new_root=new_root,
                chain="tron",
                asset="TRC20",
                amount=str(denomination),
            )

        except Exception as e:
            logger.error("Deposit failed: %s", e, exc_info=True)
            return DepositResult(
                success=False, tx_hash="", leaf_index=-1, new_root="",
                chain="tron", asset="TRC20",
                amount=str(denomination),
                error=str(e),
            )

    def _ensure_allowance(self, token_address: str, spender_address: str,
                          required_amount: int):
        """
        Check current TRC20 allowance and approve if insufficient.

        Args:
            token_address: TRC20 token contract address.
            spender_address: Address that needs the allowance (mixer contract).
            required_amount: Minimum allowance needed.

        Raises:
            RuntimeError: If approval transaction fails.
        """
        self._ensure_client()
        my_address = self.get_address()

        token = self._get_contract(token_address, abi=TRC20_TOKEN_ABI)

        # Check current allowance
        current_allowance = token.functions.allowance(my_address, spender_address)
        if current_allowance >= required_amount:
            logger.debug(
                "Allowance sufficient: %d >= %d", current_allowance, required_amount
            )
            return

        # Approve the exact required amount
        logger.info(
            "Approving %d tokens for spender %s", required_amount, spender_address
        )
        txn = (
            token.functions.approve(spender_address, required_amount)
            .with_owner(my_address)
            .fee_limit(APPROVE_FEE_LIMIT)
            .build()
        )
        signed_txn = txn.sign(self._priv_key)
        result = signed_txn.broadcast()
        tx_hash = result.get("txid", "")

        if not tx_hash:
            raise RuntimeError(f"Approve broadcast failed: {result}")

        # Wait for approval to confirm before proceeding
        self._wait_for_tx(tx_hash)
        logger.info("Approve TX confirmed: %s", tx_hash)

    def _parse_deposit_event(self, tx_info: dict) -> int:
        """
        Parse the Deposit event from transaction logs to extract the leaf index.

        The Deposit event signature: Deposit(uint256 indexed leafHash,
                                             uint256 indexed leafIndex,
                                             uint256 timestamp)

        Args:
            tx_info: Transaction info from TronGrid.

        Returns:
            Leaf index from the Deposit event, or -1 if not found.
        """
        # Deposit event topic = keccak256("Deposit(uint256,uint256,uint256)")
        # With indexed params, the topic hash is the event signature,
        # and indexed values appear as topics[1], topics[2], etc.
        import hashlib
        from eth_abi import encode

        # Pre-computed keccak256 of "Deposit(uint256,uint256,uint256)"
        # We compute it to be safe
        try:
            from eth_hash.auto import keccak
            deposit_topic = keccak(b"Deposit(uint256,uint256,uint256)").hex()
        except ImportError:
            # Fallback: use hashlib if eth_hash not available
            # keccak256 is NOT the same as sha3_256; use pysha3 or pycryptodome
            try:
                import sha3
                k = sha3.keccak_256()
                k.update(b"Deposit(uint256,uint256,uint256)")
                deposit_topic = k.hexdigest()
            except ImportError:
                # Last resort: hardcoded topic hash
                # keccak256("Deposit(uint256,uint256,uint256)")
                deposit_topic = (
                    "90890809c654f11d6e72a28fa60149770a0d11ec6c92319d6ceb2bb0a4ea1a15"
                )

        logs = tx_info.get("log", [])
        for log_entry in logs:
            topics = log_entry.get("topics", [])
            if not topics:
                continue
            if topics[0] == deposit_topic and len(topics) >= 3:
                # topics[2] = indexed leafIndex (zero-padded uint256 hex)
                leaf_index = int(topics[2], 16)
                return leaf_index

        logger.warning("Deposit event not found in transaction logs")
        return -1

    # =========================================================================
    #                          WITHDRAWAL
    # =========================================================================

    def withdraw_via_relayer(self, mixer_address: str, root: int,
                             nullifier: int, proof_points: List[int],
                             recipient: str, relayer_fee: int
                             ) -> WithdrawResult:
        """
        Withdraw from the mixer using withdrawViaRelayer.

        The service wallet (relayer) pays the energy cost and collects
        a fee from the withdrawal amount. The remainder goes to the recipient.

        Args:
            mixer_address: Mixer contract address (base58check).
            root: Merkle root at time of proof generation.
            nullifier: Nullifier to prevent double-spend.
            proof_points: 8-element list [A.x, A.y, B.x1, B.y1, B.x2, B.y2, C.x, C.y].
            recipient: Recipient Tron address (base58check).
            relayer_fee: Fee taken by the relayer (in token smallest unit).

        Returns:
            WithdrawResult with transaction details.
        """
        self._ensure_client()
        my_address = self.get_address()

        try:
            if len(proof_points) != 8:
                raise ValueError(
                    f"proof_points must have exactly 8 elements, got {len(proof_points)}"
                )

            mixer = self._get_contract(mixer_address, abi=MIXER_ABI)

            txn = (
                mixer.functions.withdrawViaRelayer(
                    root,
                    nullifier,
                    proof_points,
                    recipient,
                    relayer_fee,
                )
                .with_owner(my_address)
                .fee_limit(WITHDRAW_FEE_LIMIT)
                .build()
            )
            signed_txn = txn.sign(self._priv_key)
            result = signed_txn.broadcast()
            tx_hash = result.get("txid", "")

            if not tx_hash:
                return WithdrawResult(
                    success=False, tx_hash="",
                    nullifier=str(nullifier), recipient=recipient,
                    chain="tron", asset="TRC20", amount="0",
                    error=f"Broadcast failed: {result}",
                )

            logger.info("WithdrawViaRelayer TX broadcast: %s", tx_hash)

            # Wait for confirmation
            tx_info = self._wait_for_tx(tx_hash)

            # Read denomination from contract to compute net amount
            try:
                denom = mixer.functions.denomination()
                net_amount = denom - relayer_fee
            except Exception:
                net_amount = 0

            return WithdrawResult(
                success=True,
                tx_hash=tx_hash,
                nullifier=str(nullifier),
                recipient=recipient,
                chain="tron",
                asset="TRC20",
                amount=str(net_amount),
            )

        except Exception as e:
            logger.error("Withdrawal failed: %s", e, exc_info=True)
            return WithdrawResult(
                success=False, tx_hash="",
                nullifier=str(nullifier), recipient=recipient,
                chain="tron", asset="TRC20", amount="0",
                error=str(e),
            )

    # =========================================================================
    #                           BALANCE QUERIES
    # =========================================================================

    def get_balance(self, token_address: str = None) -> int:
        """
        Get the TRX or TRC20 token balance of the service wallet.

        Args:
            token_address: TRC20 token contract address (base58check).
                           If None, returns TRX balance in SUN.

        Returns:
            Balance in smallest unit (SUN for TRX, raw int for TRC20).
        """
        self._ensure_client()
        my_address = self.get_address()

        if token_address is None:
            # Native TRX balance
            return self._client.get_account_balance(my_address)

        # TRC20 token balance
        token = self._get_contract(token_address, abi=TRC20_TOKEN_ABI)
        return token.functions.balanceOf(my_address)

    # =========================================================================
    #                        CONTRACT READ METHODS
    # =========================================================================

    def get_root(self, mixer_address: str) -> int:
        """
        Get the current Merkle root from the mixer contract.

        Args:
            mixer_address: Mixer contract address (base58check).

        Returns:
            Current Merkle root as an integer.
        """
        self._ensure_client()
        mixer = self._get_contract(mixer_address, abi=MIXER_ABI)
        return mixer.functions.getRoot()

    def get_path(self, mixer_address: str, leaf_index: int
                 ) -> Tuple[List[int], List[bool]]:
        """
        Get the Merkle authentication path for a given leaf.

        Args:
            mixer_address: Mixer contract address (base58check).
            leaf_index: Index of the leaf in the Merkle tree.

        Returns:
            Tuple of (path, address_bits):
              - path: List of 29 sibling hashes along the path to root.
              - address_bits: List of 29 booleans indicating left/right.
        """
        self._ensure_client()
        mixer = self._get_contract(mixer_address, abi=MIXER_ABI)
        result = mixer.functions.getPath(leaf_index)

        # tronpy returns the result as a list for multi-return functions
        # result[0] = uint256[29] path, result[1] = bool[29] addressBits
        if isinstance(result, (list, tuple)) and len(result) == 2:
            path = list(result[0])
            address_bits = list(result[1])
        else:
            # Some tronpy versions return a dict
            path = list(result.get("path", result.get(0, [])))
            address_bits = list(result.get("addressBits", result.get(1, [])))

        return path, address_bits

    def is_spent(self, mixer_address: str, nullifier: int) -> bool:
        """
        Check whether a nullifier has already been spent.

        Args:
            mixer_address: Mixer contract address (base58check).
            nullifier: The nullifier to check.

        Returns:
            True if the nullifier has been used (double-spend), False otherwise.
        """
        self._ensure_client()
        mixer = self._get_contract(mixer_address, abi=MIXER_ABI)
        return mixer.functions.isSpent(nullifier)

    def get_next_leaf_index(self, mixer_address: str) -> int:
        """
        Get the next available leaf index in the mixer's Merkle tree.

        Args:
            mixer_address: Mixer contract address (base58check).

        Returns:
            Next leaf index (also equals the total number of deposits so far).
        """
        self._ensure_client()
        mixer = self._get_contract(mixer_address, abi=MIXER_ABI)
        return mixer.functions.nextLeafIndex()

    def get_denomination(self, mixer_address: str) -> int:
        """
        Get the fixed denomination amount from the mixer contract.

        Args:
            mixer_address: Mixer contract address (base58check).

        Returns:
            Denomination in smallest token unit.
        """
        self._ensure_client()
        mixer = self._get_contract(mixer_address, abi=MIXER_ABI)
        return mixer.functions.denomination()

    # =========================================================================
    #                       ADDRESS CONVERSION UTILITIES
    # =========================================================================

    @staticmethod
    def _base58_to_hex(base58_address: str) -> str:
        """
        Convert a Tron base58check address to its 20-byte hex representation
        (without the 0x41 prefix that Tron uses internally).

        Tron addresses are 21 bytes: 0x41 prefix + 20-byte address.
        For EVM-compatible log comparison, we use the last 20 bytes.

        Args:
            base58_address: Tron base58check address (T...).

        Returns:
            40-character hex string of the 20-byte address portion.
        """
        try:
            from tronpy.keys import to_hex_address
            hex_addr = to_hex_address(base58_address)
            # to_hex_address returns "41..." (42 hex chars for 21 bytes)
            # Strip the "41" prefix to get EVM-compatible 20-byte address
            if hex_addr.startswith("41") and len(hex_addr) == 42:
                return hex_addr[2:]
            return hex_addr
        except ImportError:
            raise ImportError("tronpy is required for address conversion")

    @staticmethod
    def _hex_to_base58(hex_address: str) -> str:
        """
        Convert a 20-byte hex address back to Tron base58check format.

        Args:
            hex_address: 40-character hex string (no 0x41 prefix).

        Returns:
            Tron base58check address (T...).
        """
        try:
            from tronpy.keys import to_base58check_address
            # to_base58check_address expects the full 21-byte hex with 41 prefix
            if len(hex_address) == 40:
                hex_address = "41" + hex_address
            return to_base58check_address(hex_address)
        except ImportError:
            raise ImportError("tronpy is required for address conversion")

    @staticmethod
    def _address_matches(log_address: str, expected_base58: str) -> bool:
        """
        Compare a log address (hex, possibly without 41 prefix) against
        an expected base58check address.

        Tron log entries sometimes contain addresses in different formats.
        This method normalizes both for comparison.

        Args:
            log_address: Hex address from a log entry.
            expected_base58: Expected base58check address.

        Returns:
            True if both refer to the same Tron address.
        """
        try:
            from tronpy.keys import to_hex_address
            expected_hex = to_hex_address(expected_base58)
            # Normalize: strip 41 prefix if present, compare lowercase
            norm_log = log_address.lower().lstrip("0x")
            if norm_log.startswith("41") and len(norm_log) == 42:
                norm_log = norm_log[2:]
            elif len(norm_log) == 40:
                pass  # already 20 bytes
            else:
                # Might be zero-padded (64 chars)
                norm_log = norm_log[-40:]

            norm_expected = expected_hex.lower()
            if norm_expected.startswith("41") and len(norm_expected) == 42:
                norm_expected = norm_expected[2:]

            return norm_log == norm_expected
        except Exception:
            return False

    # =========================================================================
    #                              REPR
    # =========================================================================

    def __repr__(self) -> str:
        try:
            addr = self.get_address()
        except Exception:
            addr = "not-initialized"
        return f"<TronAdapter rpc={self._rpc_url} address={addr}>"
