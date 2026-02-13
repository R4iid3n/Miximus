"""
ServiceWallet — custodial hot wallet for the Miximus mixer operator.

Manages the operator's service wallet for accepting user payments, depositing
into mixer contracts on their behalf, and executing relayed withdrawals.

Supports three chain types:
  - EVM (Ethereum, BSC, Polygon, etc.) via web3.py
  - Tron (TVM) via tronpy + TronAdapter
  - Bitcoin (UTXO) via bit + BitcoinAdapter (custodial, no contract)
"""

import logging
from typing import Dict, List, Optional

from web3 import Web3
from web3.exceptions import TransactionNotFound

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ABI snippets (duplicated from python/chain_adapters/evm.py to keep this
# module self-contained — only the functions the service wallet actually calls)
# ---------------------------------------------------------------------------

# MiximusNative: deposit (payable), withdrawViaRelayer, Deposit event
MIXER_NATIVE_ABI = [
    {
        "type": "function",
        "name": "deposit",
        "inputs": [{"name": "_leaf", "type": "uint256"}],
        "outputs": [
            {"name": "newRoot", "type": "uint256"},
            {"name": "leafIndex", "type": "uint256"},
        ],
        "stateMutability": "payable",
    },
    {
        "type": "function",
        "name": "withdrawViaRelayer",
        "inputs": [
            {"name": "_root", "type": "uint256"},
            {"name": "_nullifier", "type": "uint256"},
            {"name": "_proof", "type": "uint256[8]"},
            {"name": "_recipient", "type": "address"},
            {"name": "_relayerFee", "type": "uint256"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "event",
        "name": "Deposit",
        "inputs": [
            {"name": "leafHash", "type": "uint256", "indexed": True},
            {"name": "leafIndex", "type": "uint256", "indexed": True},
            {"name": "timestamp", "type": "uint256", "indexed": False},
        ],
    },
]

# MiximusERC20: deposit (nonpayable), withdrawViaRelayer, Deposit event
MIXER_ERC20_ABI = [
    {
        "type": "function",
        "name": "deposit",
        "inputs": [{"name": "_leaf", "type": "uint256"}],
        "outputs": [
            {"name": "newRoot", "type": "uint256"},
            {"name": "leafIndex", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "withdrawViaRelayer",
        "inputs": [
            {"name": "_root", "type": "uint256"},
            {"name": "_nullifier", "type": "uint256"},
            {"name": "_proof", "type": "uint256[8]"},
            {"name": "_recipient", "type": "address"},
            {"name": "_relayerFee", "type": "uint256"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "event",
        "name": "Deposit",
        "inputs": [
            {"name": "leafHash", "type": "uint256", "indexed": True},
            {"name": "leafIndex", "type": "uint256", "indexed": True},
            {"name": "timestamp", "type": "uint256", "indexed": False},
        ],
    },
]

# Minimal ERC20 ABI: approve + Transfer event (for payment verification)
ERC20_TOKEN_ABI = [
    {
        "type": "function",
        "name": "approve",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "type": "event",
        "name": "Transfer",
        "inputs": [
            {"name": "from", "type": "address", "indexed": True},
            {"name": "to", "type": "address", "indexed": True},
            {"name": "value", "type": "uint256", "indexed": False},
        ],
    },
]


# ---------------------------------------------------------------------------
# ServiceWallet
# ---------------------------------------------------------------------------

class ServiceWallet:
    """
    Operator hot wallet that interacts with mixer contracts on behalf of users.

    Typical custodial flow:
        1. User sends funds to the service wallet address (native or ERC20).
        2. Backend calls ``verify_payment`` to confirm the TX on-chain.
        3. Backend calls ``deposit_to_mixer`` to deposit into the mixer contract.
        4. Later, ``withdraw_via_relayer`` executes the withdrawal for the user.
    """

    # Default gas limits (can be overridden per-call via kwargs if needed)
    DEFAULT_DEPOSIT_GAS = 500_000
    DEFAULT_WITHDRAW_GAS = 600_000
    DEFAULT_APPROVE_GAS = 100_000

    def __init__(self, private_key: str, rpc_urls: Dict[str, str]):
        """
        Initialize the service wallet.

        Parameters
        ----------
        private_key : str
            Hex-encoded private key of the operator's hot wallet.
            Must include the ``0x`` prefix.
        rpc_urls : dict
            Mapping of ``chain_id`` (e.g. ``"ethereum"``, ``"bsc"``) to
            the corresponding JSON-RPC endpoint URL.
        """
        if not private_key or not private_key.startswith('0x'):
            raise ValueError("private_key must be a hex string with 0x prefix")

        self._private_key = private_key
        self._rpc_urls = dict(rpc_urls)

        # Derive the address once — same for every EVM chain
        self._account = Web3().eth.account.from_key(self._private_key)
        self._address: str = self._account.address

        # Lazy connection cache: (chain, rpc_url) -> Web3 instance
        self._web3_cache: Dict[str, Web3] = {}

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_address(self) -> str:
        """Return the hot wallet's checksummed EVM address."""
        return self._address

    def get_web3(self, chain: str, rpc_url: Optional[str] = None) -> Web3:
        """
        Return a Web3 instance for *chain*, creating one if it does not
        already exist in the cache.

        Parameters
        ----------
        chain : str
            Chain identifier (e.g. ``"ethereum"``).
        rpc_url : str, optional
            Override the RPC URL.  If not provided the URL from the
            ``rpc_urls`` dict passed at construction time is used.

        Raises
        ------
        ValueError
            If no RPC URL is available for the requested chain.
        """
        url = rpc_url or self._rpc_urls.get(chain)
        if not url:
            raise ValueError(f"No RPC URL configured for chain '{chain}'")

        cache_key = f"{chain}:{url}"
        if cache_key not in self._web3_cache:
            w3 = Web3(Web3.HTTPProvider(url))
            if not w3.is_connected():
                raise ConnectionError(
                    f"Failed to connect to {chain} at {url}"
                )
            self._web3_cache[cache_key] = w3
            logger.info("Connected to %s via %s", chain, url)

        return self._web3_cache[cache_key]

    # ------------------------------------------------------------------
    # 1. Verify a user's payment TX
    # ------------------------------------------------------------------

    def verify_payment(
        self,
        chain: str,
        rpc_url: str,
        tx_hash: str,
        expected_amount: int,
        is_native: bool = True,
    ) -> dict:
        """
        Verify that a user payment transaction exists on-chain and matches
        the expected amount.

        For **native** transfers the ``tx.value`` field is checked.
        For **ERC20** transfers the ``Transfer`` event log is decoded.

        Parameters
        ----------
        chain : str
            Chain identifier.
        rpc_url : str
            RPC endpoint to query.
        tx_hash : str
            Transaction hash to verify.
        expected_amount : int
            Expected payment amount in the token's smallest unit (wei, etc.).
        is_native : bool
            ``True`` for native currency, ``False`` for ERC20 tokens.

        Returns
        -------
        dict
            ``{verified, confirmations, amount, sender, error}``
        """
        result = {
            "verified": False,
            "confirmations": 0,
            "amount": "0",
            "sender": "",
            "error": None,
        }

        try:
            w3 = self.get_web3(chain, rpc_url)
            tx = w3.eth.get_transaction(tx_hash)
            receipt = w3.eth.get_transaction_receipt(tx_hash)

            # Must have succeeded
            if receipt.status != 1:
                result["error"] = "Transaction reverted"
                return result

            # Confirmations
            current_block = w3.eth.block_number
            result["confirmations"] = max(0, current_block - receipt.blockNumber)

            result["sender"] = tx["from"]

            if is_native:
                # Native transfer: check value and recipient
                amount = tx.value
                result["amount"] = str(amount)

                if Web3.to_checksum_address(tx["to"]) != self._address:
                    result["error"] = (
                        f"TX recipient {tx['to']} does not match "
                        f"service wallet {self._address}"
                    )
                    return result

                if amount < expected_amount:
                    result["error"] = (
                        f"Insufficient amount: got {amount}, "
                        f"expected {expected_amount}"
                    )
                    return result

                result["verified"] = True

            else:
                # ERC20 transfer: decode Transfer event from logs
                erc20_contract = w3.eth.contract(abi=ERC20_TOKEN_ABI)
                transfer_events = (
                    erc20_contract.events.Transfer().process_receipt(receipt)
                )

                matched = False
                for event in transfer_events:
                    to_addr = Web3.to_checksum_address(event.args["to"])
                    if to_addr == self._address:
                        amount = event.args["value"]
                        result["amount"] = str(amount)
                        result["sender"] = event.args["from"]

                        if amount < expected_amount:
                            result["error"] = (
                                f"Insufficient amount: got {amount}, "
                                f"expected {expected_amount}"
                            )
                            return result

                        matched = True
                        result["verified"] = True
                        break

                if not matched:
                    result["error"] = (
                        "No ERC20 Transfer event to service wallet found in TX"
                    )

        except TransactionNotFound:
            result["error"] = f"Transaction {tx_hash} not found on {chain}"
        except Exception as exc:
            result["error"] = f"Verification failed: {exc}"
            logger.exception("verify_payment error on %s", chain)

        return result

    # ------------------------------------------------------------------
    # 2. Deposit into mixer contract
    # ------------------------------------------------------------------

    def deposit_to_mixer(
        self,
        rpc_url: str,
        contract_address: str,
        leaf_hash: int,
        denomination: int,
        is_native: bool = True,
        token_address: Optional[str] = None,
    ) -> dict:
        """
        Deposit into the mixer contract from the hot wallet.

        For native deposits ``denomination`` wei is sent as ``msg.value``.
        For ERC20 deposits the token is first approved, then
        ``deposit(_leaf)`` is called (no value).

        Parameters
        ----------
        rpc_url : str
            RPC endpoint for the target chain.
        contract_address : str
            Address of the deployed Miximus mixer contract.
        leaf_hash : int
            The leaf commitment hash to insert into the Merkle tree.
        denomination : int
            Fixed denomination of the mixer pool (in smallest unit).
        is_native : bool
            ``True`` for native currency pools, ``False`` for ERC20.
        token_address : str, optional
            ERC20 token contract address (required when ``is_native=False``).

        Returns
        -------
        dict
            ``{success, tx_hash, leaf_index, error}``
        """
        result = {
            "success": False,
            "tx_hash": "",
            "leaf_index": -1,
            "error": None,
        }

        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url))
            if not w3.is_connected():
                result["error"] = f"Cannot connect to RPC at {rpc_url}"
                return result

            contract_addr = Web3.to_checksum_address(contract_address)
            nonce = w3.eth.get_transaction_count(self._address)
            gas_price = w3.eth.gas_price

            if not is_native:
                # ---- ERC20 path: approve then deposit ----
                if not token_address:
                    result["error"] = (
                        "token_address is required for ERC20 deposits"
                    )
                    return result

                token_addr = Web3.to_checksum_address(token_address)
                token_contract = w3.eth.contract(
                    address=token_addr, abi=ERC20_TOKEN_ABI
                )

                # Step 1: approve
                approve_tx = token_contract.functions.approve(
                    contract_addr, denomination
                ).build_transaction({
                    "from": self._address,
                    "nonce": nonce,
                    "gas": self.DEFAULT_APPROVE_GAS,
                    "gasPrice": gas_price,
                })
                signed_approve = w3.eth.account.sign_transaction(
                    approve_tx, self._private_key
                )
                approve_hash = w3.eth.send_raw_transaction(
                    signed_approve.raw_transaction
                )
                w3.eth.wait_for_transaction_receipt(approve_hash)
                nonce += 1

                # Step 2: deposit (no msg.value for ERC20)
                mixer = w3.eth.contract(
                    address=contract_addr, abi=MIXER_ERC20_ABI
                )
                deposit_tx = mixer.functions.deposit(leaf_hash).build_transaction({
                    "from": self._address,
                    "nonce": nonce,
                    "gas": self.DEFAULT_DEPOSIT_GAS,
                    "gasPrice": gas_price,
                })

            else:
                # ---- Native path: send value ----
                mixer = w3.eth.contract(
                    address=contract_addr, abi=MIXER_NATIVE_ABI
                )
                deposit_tx = mixer.functions.deposit(leaf_hash).build_transaction({
                    "from": self._address,
                    "value": denomination,
                    "nonce": nonce,
                    "gas": self.DEFAULT_DEPOSIT_GAS,
                    "gasPrice": gas_price,
                })

            signed = w3.eth.account.sign_transaction(
                deposit_tx, self._private_key
            )
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt.status != 1:
                result["error"] = "Deposit transaction reverted"
                result["tx_hash"] = tx_hash.hex()
                return result

            result["tx_hash"] = tx_hash.hex()
            result["success"] = True

            # Parse the Deposit event to get the leaf index
            abi = MIXER_ERC20_ABI if not is_native else MIXER_NATIVE_ABI
            mixer_for_events = w3.eth.contract(
                address=contract_addr, abi=abi
            )
            deposit_events = (
                mixer_for_events.events.Deposit().process_receipt(receipt)
            )
            if deposit_events:
                result["leaf_index"] = deposit_events[0].args.leafIndex

            logger.info(
                "Deposited leaf=%s into %s (tx=%s, idx=%s)",
                leaf_hash, contract_address, result["tx_hash"],
                result["leaf_index"],
            )

        except Exception as exc:
            result["error"] = f"Deposit failed: {exc}"
            logger.exception("deposit_to_mixer error")

        return result

    # ------------------------------------------------------------------
    # 3. Withdraw via relayer
    # ------------------------------------------------------------------

    def withdraw_via_relayer(
        self,
        rpc_url: str,
        contract_address: str,
        root: int,
        nullifier: int,
        proof_points: List[int],
        recipient: str,
        relayer_fee: int,
        is_native: bool = True,
    ) -> dict:
        """
        Call ``withdrawViaRelayer`` on the mixer contract.

        The hot wallet pays gas; the relayer fee is deducted from the
        denomination and sent to the hot wallet by the contract.

        Parameters
        ----------
        rpc_url : str
            RPC endpoint for the target chain.
        contract_address : str
            Mixer contract address.
        root : int
            Merkle root used in the proof.
        nullifier : int
            Nullifier to prevent double-spends.
        proof_points : list of int
            Eight uint256 values encoding the Groth16 proof.
        recipient : str
            Address that will receive the withdrawn funds.
        relayer_fee : int
            Fee (in smallest unit) kept by the relayer/operator.
        is_native : bool
            ``True`` for native currency pools, ``False`` for ERC20.

        Returns
        -------
        dict
            ``{success, tx_hash, error}``
        """
        result = {
            "success": False,
            "tx_hash": "",
            "error": None,
        }

        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url))
            if not w3.is_connected():
                result["error"] = f"Cannot connect to RPC at {rpc_url}"
                return result

            contract_addr = Web3.to_checksum_address(contract_address)
            recipient_addr = Web3.to_checksum_address(recipient)

            abi = MIXER_ERC20_ABI if not is_native else MIXER_NATIVE_ABI
            mixer = w3.eth.contract(address=contract_addr, abi=abi)

            nonce = w3.eth.get_transaction_count(self._address)
            gas_price = w3.eth.gas_price

            # Ensure proof_points is exactly 8 uint256 values
            if len(proof_points) != 8:
                result["error"] = (
                    f"proof_points must have exactly 8 elements, "
                    f"got {len(proof_points)}"
                )
                return result

            tx = mixer.functions.withdrawViaRelayer(
                root,
                nullifier,
                proof_points,
                recipient_addr,
                relayer_fee,
            ).build_transaction({
                "from": self._address,
                "nonce": nonce,
                "gas": self.DEFAULT_WITHDRAW_GAS,
                "gasPrice": gas_price,
            })

            signed = w3.eth.account.sign_transaction(tx, self._private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

            result["tx_hash"] = tx_hash.hex()

            if receipt.status != 1:
                result["error"] = "withdrawViaRelayer transaction reverted"
                return result

            result["success"] = True
            logger.info(
                "Relayed withdrawal to %s from %s (tx=%s)",
                recipient, contract_address, result["tx_hash"],
            )

        except Exception as exc:
            result["error"] = f"Withdrawal failed: {exc}"
            logger.exception("withdraw_via_relayer error")

        return result

    # ------------------------------------------------------------------
    # 4. Balance query
    # ------------------------------------------------------------------

    def get_native_balance(self, rpc_url: str) -> int:
        """
        Return the hot wallet's native token balance (in wei or equivalent).

        Parameters
        ----------
        rpc_url : str
            RPC endpoint to query.

        Returns
        -------
        int
            Balance in the chain's smallest denomination.
        """
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        return w3.eth.get_balance(self._address)


# ---------------------------------------------------------------------------
# Chain type classification
# ---------------------------------------------------------------------------

# Map chain identifiers to their type
CHAIN_TYPES = {
    'ethereum': 'evm', 'bsc': 'evm', 'polygon': 'evm', 'avalanche': 'evm',
    'fantom': 'evm', 'arbitrum': 'evm', 'optimism': 'evm', 'base': 'evm',
    'tron': 'tvm',
    'bitcoin': 'utxo', 'litecoin': 'utxo', 'bitcoin-cash': 'utxo',
    'dogecoin': 'utxo', 'dash': 'utxo', 'zcash': 'utxo',
}


def get_chain_type(chain: str) -> str:
    """Return 'evm', 'tvm', or 'utxo' for a chain identifier."""
    return CHAIN_TYPES.get(chain, 'evm')


# ---------------------------------------------------------------------------
# MultiChainWallet — facade routing to the right adapter per chain
# ---------------------------------------------------------------------------

class MultiChainWallet:
    """
    Multi-chain wallet facade. Routes operations to the correct adapter
    based on chain type (EVM/Tron/Bitcoin).
    """

    def __init__(self, private_key: str, rpc_urls: Dict[str, str]):
        self._private_key = private_key
        self._rpc_urls = dict(rpc_urls)

        # EVM wallet (always available)
        self._evm_wallet = ServiceWallet(private_key, rpc_urls)

        # Tron adapter (lazy)
        self._tron_adapter = None

        # Bitcoin adapter (lazy, keyed by network)
        self._btc_adapters: Dict[str, object] = {}

    def get_evm_address(self) -> str:
        return self._evm_wallet.get_address()

    def _get_tron_adapter(self, rpc_url: str = None):
        if self._tron_adapter is None:
            import sys, os
            # Add project root to path so we can import chain_adapters
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from python.chain_adapters.tron import TronAdapter
            url = rpc_url or self._rpc_urls.get('tron', 'https://nile.trongrid.io')
            self._tron_adapter = TronAdapter(self._private_key, url)
        return self._tron_adapter

    def _get_btc_adapter(self, network: str = 'testnet'):
        if network not in self._btc_adapters:
            import sys, os
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from python.chain_adapters.btc import BitcoinAdapter
            self._btc_adapters[network] = BitcoinAdapter(self._private_key, network=network)
        return self._btc_adapters[network]

    # ------------------------------------------------------------------
    # Verify payment (multi-chain)
    # ------------------------------------------------------------------

    def verify_payment(self, chain: str, rpc_url: str, tx_hash: str,
                       expected_amount: int, is_native: bool = True,
                       token_address: str = None,
                       network_mode: str = 'testnet') -> dict:
        chain_type = get_chain_type(chain)

        if chain_type == 'tvm':
            adapter = self._get_tron_adapter(rpc_url)
            result = adapter.verify_payment(tx_hash, expected_amount, token_address or '')
            # Normalize to match EVM format
            return {
                'verified': result.get('verified', False),
                'confirmations': 19 if result.get('verified') else 0,
                'amount': str(result.get('amount', 0)),
                'sender': result.get('sender', ''),
                'error': result.get('error'),
            }

        if chain_type == 'utxo':
            adapter = self._get_btc_adapter(network_mode)
            try:
                result = adapter.verify_payment(tx_hash, expected_amount)
                return {
                    'verified': result.get('verified', False),
                    'confirmations': result.get('confirmations', 0),
                    'amount': str(result.get('amount', 0)),
                    'sender': result.get('sender', ''),
                    'error': None,
                }
            except Exception as e:
                return {
                    'verified': False, 'confirmations': 0,
                    'amount': '0', 'sender': '', 'error': str(e),
                }

        # Default: EVM
        return self._evm_wallet.verify_payment(
            chain=chain, rpc_url=rpc_url, tx_hash=tx_hash,
            expected_amount=expected_amount, is_native=is_native,
        )

    # ------------------------------------------------------------------
    # Deposit to mixer (multi-chain)
    # ------------------------------------------------------------------

    def deposit_to_mixer(self, chain: str, rpc_url: str,
                         contract_address: str, leaf_hash: int,
                         denomination: int, is_native: bool = True,
                         token_address: str = None,
                         network_mode: str = 'testnet') -> dict:
        chain_type = get_chain_type(chain)

        if chain_type == 'utxo':
            # Bitcoin is custodial — no contract deposit. Return success immediately.
            return {
                'success': True, 'tx_hash': 'custodial',
                'leaf_index': -1, 'error': None,
            }

        if chain_type == 'tvm':
            adapter = self._get_tron_adapter(rpc_url)
            result = adapter.deposit_to_mixer(
                contract_address, leaf_hash, denomination, token_address or '',
            )
            return {
                'success': result.success,
                'tx_hash': result.tx_hash,
                'leaf_index': result.leaf_index,
                'error': result.error,
            }

        # Default: EVM
        return self._evm_wallet.deposit_to_mixer(
            rpc_url=rpc_url, contract_address=contract_address,
            leaf_hash=leaf_hash, denomination=denomination,
            is_native=is_native, token_address=token_address,
        )

    # ------------------------------------------------------------------
    # Withdraw via relayer (multi-chain)
    # ------------------------------------------------------------------

    def withdraw_via_relayer(self, chain: str, rpc_url: str,
                             contract_address: str, root: int,
                             nullifier: int, proof_points: List[int],
                             recipient: str, relayer_fee: int,
                             is_native: bool = True,
                             payout_amount: int = 0,
                             network_mode: str = 'testnet') -> dict:
        chain_type = get_chain_type(chain)

        if chain_type == 'utxo':
            # Bitcoin: send payout directly from UTXO pool (no contract)
            adapter = self._get_btc_adapter(network_mode)
            try:
                tx_hash = adapter.send_btc(recipient, payout_amount)
                return {'success': True, 'tx_hash': tx_hash, 'error': None}
            except Exception as e:
                return {'success': False, 'tx_hash': '', 'error': str(e)}

        if chain_type == 'tvm':
            adapter = self._get_tron_adapter(rpc_url)
            result = adapter.withdraw_via_relayer(
                contract_address, root, nullifier,
                proof_points, recipient, relayer_fee,
            )
            return {
                'success': result.success,
                'tx_hash': result.tx_hash,
                'error': result.error,
            }

        # Default: EVM
        return self._evm_wallet.withdraw_via_relayer(
            rpc_url=rpc_url, contract_address=contract_address,
            root=root, nullifier=nullifier, proof_points=proof_points,
            recipient=recipient, relayer_fee=relayer_fee, is_native=is_native,
        )
