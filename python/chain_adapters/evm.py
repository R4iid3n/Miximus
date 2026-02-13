"""
EVM Chain Adapter

Handles all EVM-compatible chains:
  Ethereum, BSC, Polygon, Avalanche, Arbitrum, Base, Cronos,
  Moonbeam, Ethereum Classic, Qtum, VeChain, Optimism

Uses web3.py to interact with smart contracts.
"""

import json
from typing import Optional, Tuple, List

from .base import (
    ChainAdapter, ChainType, DepositResult, BatchDepositResult,
    WithdrawResult, BatchWithdrawResult, ProofData
)


# ABI for MiximusNative
NATIVE_ABI = json.loads("""[
    {"type":"function","name":"deposit","inputs":[{"name":"_leaf","type":"uint256"}],"outputs":[{"name":"newRoot","type":"uint256"},{"name":"leafIndex","type":"uint256"}],"stateMutability":"payable"},
    {"type":"function","name":"batchDeposit","inputs":[{"name":"_leaves","type":"uint256[]"}],"outputs":[{"name":"startIndex","type":"uint256"}],"stateMutability":"payable"},
    {"type":"function","name":"withdraw","inputs":[{"name":"_root","type":"uint256"},{"name":"_nullifier","type":"uint256"},{"name":"_proof","type":"uint256[8]"}],"outputs":[],"stateMutability":"nonpayable"},
    {"type":"function","name":"batchWithdraw","inputs":[{"name":"_roots","type":"uint256[]"},{"name":"_nullifiers","type":"uint256[]"},{"name":"_proofs","type":"uint256[8][]"}],"outputs":[],"stateMutability":"nonpayable"},
    {"type":"function","name":"withdrawViaRelayer","inputs":[{"name":"_root","type":"uint256"},{"name":"_nullifier","type":"uint256"},{"name":"_proof","type":"uint256[8]"},{"name":"_recipient","type":"address"},{"name":"_relayerFee","type":"uint256"}],"outputs":[],"stateMutability":"nonpayable"},
    {"type":"function","name":"getRoot","inputs":[],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},
    {"type":"function","name":"getPath","inputs":[{"name":"_leafIndex","type":"uint256"}],"outputs":[{"name":"path","type":"uint256[29]"},{"name":"addressBits","type":"bool[29]"}],"stateMutability":"view"},
    {"type":"function","name":"isSpent","inputs":[{"name":"_nullifier","type":"uint256"}],"outputs":[{"name":"","type":"bool"}],"stateMutability":"view"},
    {"type":"function","name":"getExtHash","inputs":[],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},
    {"type":"function","name":"denomination","inputs":[],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},
    {"type":"function","name":"makeLeafHash","inputs":[{"name":"_secret","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"pure"},
    {"type":"event","name":"Deposit","inputs":[{"name":"leafHash","type":"uint256","indexed":true},{"name":"leafIndex","type":"uint256","indexed":true},{"name":"timestamp","type":"uint256","indexed":false}]},
    {"type":"event","name":"Withdrawal","inputs":[{"name":"recipient","type":"address","indexed":true},{"name":"nullifier","type":"uint256","indexed":false},{"name":"timestamp","type":"uint256","indexed":false}]}
]""")

# ABI for MiximusERC20 — adds token-specific functions
ERC20_ABI = json.loads("""[
    {"type":"function","name":"deposit","inputs":[{"name":"_leaf","type":"uint256"}],"outputs":[{"name":"newRoot","type":"uint256"},{"name":"leafIndex","type":"uint256"}],"stateMutability":"nonpayable"},
    {"type":"function","name":"batchDeposit","inputs":[{"name":"_leaves","type":"uint256[]"}],"outputs":[{"name":"startIndex","type":"uint256"}],"stateMutability":"nonpayable"},
    {"type":"function","name":"withdraw","inputs":[{"name":"_root","type":"uint256"},{"name":"_nullifier","type":"uint256"},{"name":"_proof","type":"uint256[8]"}],"outputs":[],"stateMutability":"nonpayable"},
    {"type":"function","name":"batchWithdraw","inputs":[{"name":"_roots","type":"uint256[]"},{"name":"_nullifiers","type":"uint256[]"},{"name":"_proofs","type":"uint256[8][]"}],"outputs":[],"stateMutability":"nonpayable"},
    {"type":"function","name":"withdrawViaRelayer","inputs":[{"name":"_root","type":"uint256"},{"name":"_nullifier","type":"uint256"},{"name":"_proof","type":"uint256[8]"},{"name":"_recipient","type":"address"},{"name":"_relayerFee","type":"uint256"}],"outputs":[],"stateMutability":"nonpayable"},
    {"type":"function","name":"getRoot","inputs":[],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},
    {"type":"function","name":"getPath","inputs":[{"name":"_leafIndex","type":"uint256"}],"outputs":[{"name":"path","type":"uint256[29]"},{"name":"addressBits","type":"bool[29]"}],"stateMutability":"view"},
    {"type":"function","name":"isSpent","inputs":[{"name":"_nullifier","type":"uint256"}],"outputs":[{"name":"","type":"bool"}],"stateMutability":"view"},
    {"type":"function","name":"getExtHash","inputs":[],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},
    {"type":"function","name":"denomination","inputs":[],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},
    {"type":"function","name":"token","inputs":[],"outputs":[{"name":"","type":"address"}],"stateMutability":"view"},
    {"type":"event","name":"Deposit","inputs":[{"name":"leafHash","type":"uint256","indexed":true},{"name":"leafIndex","type":"uint256","indexed":true},{"name":"timestamp","type":"uint256","indexed":false}]},
    {"type":"event","name":"Withdrawal","inputs":[{"name":"recipient","type":"address","indexed":true},{"name":"nullifier","type":"uint256","indexed":false},{"name":"timestamp","type":"uint256","indexed":false}]}
]""")

# Minimal ERC20 ABI for token approval
ERC20_TOKEN_ABI = json.loads("""[
    {"type":"function","name":"approve","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable"},
    {"type":"function","name":"balanceOf","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"},
    {"type":"function","name":"allowance","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"outputs":[{"name":"","type":"uint256"}],"stateMutability":"view"}
]""")


class EVMAdapter(ChainAdapter):
    """
    EVM chain adapter — works on ANY EVM-compatible blockchain.

    Covers: Ethereum, BSC, Polygon, Avalanche, Arbitrum, Base,
            Cronos, Moonbeam, Ethereum Classic, Qtum, VeChain, Optimism
    """

    def __init__(self, chain_id: str, rpc_url: str, native_symbol: str,
                 denomination: int, contract_address: str,
                 is_token: bool = False, token_address: Optional[str] = None):
        super().__init__(chain_id, ChainType.EVM, rpc_url, native_symbol, denomination)
        self.contract_address = contract_address
        self.is_token = is_token
        self.token_address = token_address
        self.w3 = None
        self.contract = None

    def connect(self) -> bool:
        """Connect to the EVM node via web3.py"""
        try:
            from web3 import Web3
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            abi = ERC20_ABI if self.is_token else NATIVE_ABI
            self.contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(self.contract_address),
                abi=abi
            )
            return self.w3.is_connected()
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def get_root(self) -> int:
        return self.contract.functions.getRoot().call()

    def get_path(self, leaf_index: int) -> Tuple[List[int], List[bool]]:
        path, addr_bits = self.contract.functions.getPath(leaf_index).call()
        return list(path), list(addr_bits)

    def is_spent(self, nullifier: int) -> bool:
        return self.contract.functions.isSpent(nullifier).call()

    def get_balance(self) -> int:
        if self.is_token:
            from web3 import Web3
            token = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.token_address),
                abi=ERC20_TOKEN_ABI
            )
            return token.functions.balanceOf(self.contract_address).call()
        else:
            return self.w3.eth.get_balance(self.contract_address)

    def deposit(self, leaf_hash: int, private_key: str) -> DepositResult:
        """Deposit native currency or ERC20 token"""
        try:
            account = self.w3.eth.account.from_key(private_key)
            nonce = self.w3.eth.get_transaction_count(account.address)

            if self.is_token:
                # First approve the token transfer
                self._approve_token(account, private_key, nonce)
                nonce += 1
                # Then deposit (no msg.value for ERC20)
                tx = self.contract.functions.deposit(leaf_hash).build_transaction({
                    'from': account.address,
                    'nonce': nonce,
                    'gas': 500000,
                    'gasPrice': self.w3.eth.gas_price,
                })
            else:
                # Native currency deposit (send value)
                tx = self.contract.functions.deposit(leaf_hash).build_transaction({
                    'from': account.address,
                    'value': self.denomination,
                    'nonce': nonce,
                    'gas': 500000,
                    'gasPrice': self.w3.eth.gas_price,
                })

            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

            # Parse deposit event
            deposit_events = self.contract.events.Deposit().process_receipt(receipt)
            if deposit_events:
                event = deposit_events[0]
                return DepositResult(
                    success=True,
                    tx_hash=tx_hash.hex(),
                    leaf_index=event.args.leafIndex,
                    new_root=str(self.get_root()),
                    chain=self.chain_id,
                    asset=self.native_symbol,
                    amount=str(self.denomination),
                )

            return DepositResult(
                success=receipt.status == 1,
                tx_hash=tx_hash.hex(),
                leaf_index=-1,
                new_root="",
                chain=self.chain_id,
                asset=self.native_symbol,
                amount=str(self.denomination),
            )
        except Exception as e:
            return DepositResult(
                success=False, tx_hash="", leaf_index=-1, new_root="",
                chain=self.chain_id, asset=self.native_symbol,
                amount=str(self.denomination), error=str(e),
            )

    def withdraw(self, proof: ProofData, recipient: str,
                 private_key: str) -> WithdrawResult:
        """Withdraw using zkSNARK proof"""
        try:
            account = self.w3.eth.account.from_key(private_key)
            nonce = self.w3.eth.get_transaction_count(account.address)

            proof_array = proof.proof_points

            tx = self.contract.functions.withdraw(
                proof.root,
                proof.nullifier,
                proof_array
            ).build_transaction({
                'from': account.address,
                'nonce': nonce,
                'gas': 500000,
                'gasPrice': self.w3.eth.gas_price,
            })

            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

            return WithdrawResult(
                success=receipt.status == 1,
                tx_hash=tx_hash.hex(),
                nullifier=str(proof.nullifier),
                recipient=recipient,
                chain=self.chain_id,
                asset=self.native_symbol,
                amount=str(self.denomination),
            )
        except Exception as e:
            return WithdrawResult(
                success=False, tx_hash="", nullifier=str(proof.nullifier),
                recipient=recipient, chain=self.chain_id,
                asset=self.native_symbol, amount=str(self.denomination),
                error=str(e),
            )

    def withdraw_via_relayer(self, proof: ProofData, recipient: str,
                             relayer_fee: int, private_key: str) -> WithdrawResult:
        """Withdraw using withdrawViaRelayer — relayer (hot wallet) pays gas, collects fee."""
        try:
            account = self.w3.eth.account.from_key(private_key)
            nonce = self.w3.eth.get_transaction_count(account.address)

            tx = self.contract.functions.withdrawViaRelayer(
                proof.root,
                proof.nullifier,
                proof.proof_points,
                self.w3.to_checksum_address(recipient),
                relayer_fee
            ).build_transaction({
                'from': account.address,
                'nonce': nonce,
                'gas': 600000,
                'gasPrice': self.w3.eth.gas_price,
            })

            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

            return WithdrawResult(
                success=receipt.status == 1,
                tx_hash=tx_hash.hex(),
                nullifier=str(proof.nullifier),
                recipient=recipient,
                chain=self.chain_id,
                asset=self.native_symbol,
                amount=str(self.denomination - relayer_fee),
            )
        except Exception as e:
            return WithdrawResult(
                success=False, tx_hash="", nullifier=str(proof.nullifier),
                recipient=recipient, chain=self.chain_id,
                asset=self.native_symbol, amount=str(self.denomination),
                error=str(e),
            )

    def _approve_token(self, account, private_key: str, nonce: int):
        """Approve token transfer to mixer contract"""
        from web3 import Web3
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.token_address),
            abi=ERC20_TOKEN_ABI
        )
        tx = token.functions.approve(
            self.contract_address, self.denomination
        ).build_transaction({
            'from': account.address,
            'nonce': nonce,
            'gas': 100000,
            'gasPrice': self.w3.eth.gas_price,
        })
        signed = self.w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def batch_deposit(self, leaf_hashes: List[int], private_key: str) -> BatchDepositResult:
        """Batch deposit — insert multiple leaves in a single transaction"""
        try:
            account = self.w3.eth.account.from_key(private_key)
            nonce = self.w3.eth.get_transaction_count(account.address)
            count = len(leaf_hashes)

            if self.is_token:
                # Approve total amount for ERC20
                total_amount = self.denomination * count
                self._approve_token_amount(account, private_key, nonce, total_amount)
                nonce += 1
                tx = self.contract.functions.batchDeposit(leaf_hashes).build_transaction({
                    'from': account.address,
                    'nonce': nonce,
                    'gas': 300000 + 200000 * count,
                    'gasPrice': self.w3.eth.gas_price,
                })
            else:
                tx = self.contract.functions.batchDeposit(leaf_hashes).build_transaction({
                    'from': account.address,
                    'value': self.denomination * count,
                    'nonce': nonce,
                    'gas': 300000 + 200000 * count,
                    'gasPrice': self.w3.eth.gas_price,
                })

            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

            # Parse all Deposit events to get leaf indices
            deposit_events = self.contract.events.Deposit().process_receipt(receipt)
            leaf_indices = [e.args.leafIndex for e in deposit_events]

            # Fallback if events not parsed
            if not leaf_indices:
                leaf_indices = list(range(count))

            return BatchDepositResult(
                success=receipt.status == 1,
                tx_hash=tx_hash.hex(),
                leaf_indices=leaf_indices,
                new_root=str(self.get_root()),
                chain=self.chain_id,
                asset=self.native_symbol,
                total_amount=str(self.denomination * count),
                count=count,
            )
        except Exception as e:
            return BatchDepositResult(
                success=False, tx_hash="", leaf_indices=[],
                new_root="", chain=self.chain_id, asset=self.native_symbol,
                total_amount=str(self.denomination * len(leaf_hashes)),
                count=len(leaf_hashes), error=str(e),
            )

    def batch_withdraw(self, proofs: List[ProofData], recipient: str,
                       private_key: str) -> BatchWithdrawResult:
        """Batch withdraw — withdraw multiple notes in a single transaction"""
        try:
            account = self.w3.eth.account.from_key(private_key)
            nonce = self.w3.eth.get_transaction_count(account.address)
            count = len(proofs)

            roots = [p.root for p in proofs]
            nullifiers = [p.nullifier for p in proofs]
            proof_arrays = [p.proof_points for p in proofs]

            tx = self.contract.functions.batchWithdraw(
                roots, nullifiers, proof_arrays
            ).build_transaction({
                'from': account.address,
                'nonce': nonce,
                'gas': 300000 + 400000 * count,
                'gasPrice': self.w3.eth.gas_price,
            })

            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

            return BatchWithdrawResult(
                success=receipt.status == 1,
                tx_hash=tx_hash.hex(),
                nullifiers=[str(n) for n in nullifiers],
                recipient=recipient,
                chain=self.chain_id,
                asset=self.native_symbol,
                total_amount=str(self.denomination * count),
                count=count,
            )
        except Exception as e:
            return BatchWithdrawResult(
                success=False, tx_hash="",
                nullifiers=[str(p.nullifier) for p in proofs],
                recipient=recipient, chain=self.chain_id,
                asset=self.native_symbol,
                total_amount=str(self.denomination * len(proofs)),
                count=len(proofs), error=str(e),
            )

    def _approve_token_amount(self, account, private_key: str, nonce: int, amount: int):
        """Approve a specific token amount for the mixer contract"""
        from web3 import Web3
        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.token_address),
            abi=ERC20_TOKEN_ABI
        )
        tx = token.functions.approve(
            self.contract_address, amount
        ).build_transaction({
            'from': account.address,
            'nonce': nonce,
            'gas': 100000,
            'gasPrice': self.w3.eth.gas_price,
        })
        signed = self.w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self.w3.eth.wait_for_transaction_receipt(tx_hash)
