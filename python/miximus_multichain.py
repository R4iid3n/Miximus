"""
Miximus Multi-Chain Orchestrator

The main entry point for the multi-chain mixer. Routes operations to the
correct blockchain based on the asset being mixed.

Usage:
    from miximus_multichain import MiximusMultiChain

    mixer = MiximusMultiChain()
    mixer.list_supported_assets()

    # Deposit 1 ETH on Ethereum
    result = mixer.deposit("ETH", "ethereum", secret, private_key)

    # Withdraw on any chain
    result = mixer.withdraw("ETH", "ethereum", secret, leaf_index, recipient, private_key)

    # Same interface for ANY asset:
    mixer.deposit("USDT", "bsc", secret, private_key)       # USDT on BSC
    mixer.deposit("SOL", "solana", secret, private_key)      # SOL on Solana
    mixer.deposit("BTC", "bitcoin", secret, private_key)     # BTC via HTLC

Copyright 2024 Miximus Authors — GPL-3.0-or-later
"""

import os
import sys
import json
import secrets
from typing import Optional, Dict, List, Tuple

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

# ethsnarks Python package (for MiMC hash, verifier, etc.)
_ethsnarks_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ethsnarks-miximus', 'ethsnarks')
if _ethsnarks_path not in sys.path:
    sys.path.insert(0, _ethsnarks_path)

from chain_adapters.base import (
    ChainAdapter, ChainType, DepositResult, BatchDepositResult,
    WithdrawResult, BatchWithdrawResult, ProofData
)
from chain_adapters.registry import AssetRegistry, AssetConfig, get_adapter_for_asset


class MiximusMultiChain:
    """
    Multi-chain mixer orchestrator.

    This is the unified interface for mixing coins across ALL supported blockchains.
    It handles:
      1. Asset routing — determines which chain adapter to use
      2. Secret management — generates cryptographic secrets for deposits
      3. Proof generation — calls the native zkSNARK prover (shared across all chains)
      4. Deposit/Withdraw — delegates to chain-specific adapters
    """

    def __init__(self, config_path: str = None, native_lib_path: str = None,
                 pk_file: str = None, vk_file: str = None):
        """
        Initialize the multi-chain mixer.

        Args:
            config_path: Path to assets.json configuration
            native_lib_path: Path to libmiximus native library (.so/.dll)
            pk_file: Path to the zkSNARK proving key
            vk_file: Path to the zkSNARK verifying key (or JSON)
        """
        self.registry = AssetRegistry.load(config_path)
        self.native_lib_path = native_lib_path
        self.pk_file = pk_file
        self.vk_file = vk_file
        self._prover = None  # Lazy-loaded native prover
        self._adapters: Dict[str, ChainAdapter] = {}

    # =========================================================================
    #                          PUBLIC API
    # =========================================================================

    def list_supported_assets(self) -> List[dict]:
        """List all supported assets across all chains"""
        assets = []
        for asset in self.registry.get_all_assets():
            assets.append({
                "symbol": asset.symbol,
                "name": asset.name,
                "chain": asset.chain,
                "chain_type": asset.chain_type.value,
                "type": asset.asset_type,
                "decimals": asset.decimals,
                "token_contract": asset.contract_address,
            })
        return assets

    def list_chains(self) -> List[dict]:
        """List all supported blockchain networks"""
        chains = []
        for chain_id, config in self.registry.chains.items():
            chains.append({
                "id": chain_id,
                "name": config.get("name"),
                "type": config.get("type"),
                "native_currency": config.get("native_currency"),
            })
        return chains

    def get_asset_info(self, symbol: str, chain: str) -> Optional[dict]:
        """Get detailed info for a specific asset on a specific chain"""
        asset = self.registry.get_asset(symbol, chain)
        if not asset:
            return None
        return {
            "symbol": asset.symbol,
            "name": asset.name,
            "chain": asset.chain,
            "chain_type": asset.chain_type.value,
            "type": asset.asset_type,
            "decimals": asset.decimals,
            "denomination": asset.denomination,
            "token_contract": asset.contract_address,
            "mixer_contract": asset.mixer_contract,
        }

    def generate_secret(self) -> int:
        """
        Generate a cryptographically secure random secret.
        This is the core privacy element — NEVER share this.
        """
        # Generate 31 bytes (248 bits) to stay within the BN254 scalar field
        return int.from_bytes(secrets.token_bytes(31), 'big')

    def compute_leaf_hash(self, secret: int) -> int:
        """
        Compute the leaf hash from a secret: leaf_hash = MiMC_hash([secret])
        Uses ethsnarks MiMC with Keccak-256 round constants (matches circuit).
        """
        from ethsnarks.mimc import mimc_hash
        return mimc_hash([secret])

    def compute_nullifier(self, secret: int, leaf_index: int) -> int:
        """
        Compute the nullifier for a deposit.
        nullifier = MiMC_hash([leaf_index, secret])
        """
        prover = self._get_prover()
        if prover:
            return prover.nullifier(secret, leaf_index)
        else:
            from ethsnarks.mimc import mimc_hash
            return mimc_hash([leaf_index, secret])

    def deposit(self, symbol: str, chain: str, secret: int,
                private_key: str) -> DepositResult:
        """
        Deposit funds into the mixer.

        Args:
            symbol: Asset symbol (e.g., "ETH", "USDT", "BTC")
            chain: Chain identifier (e.g., "ethereum", "bsc", "bitcoin")
            secret: The spending secret (from generate_secret())
            private_key: Wallet private key for the transaction

        Returns:
            DepositResult with transaction details
        """
        asset = self.registry.get_asset(symbol, chain)
        if not asset:
            return DepositResult(
                success=False, tx_hash="", leaf_index=-1, new_root="",
                chain=chain, asset=symbol, amount="0",
                error=f"Asset {symbol} not found on chain {chain}"
            )

        leaf_hash = self.compute_leaf_hash(secret)
        adapter = self._get_adapter(asset)
        return adapter.deposit(leaf_hash, private_key)

    def batch_deposit(self, symbol: str, chain: str, count: int,
                      private_key: str) -> dict:
        """
        Batch deposit — deposit N units in a single transaction, receive N secrets.

        Args:
            symbol: Asset symbol (e.g., "ETH", "USDT")
            chain: Chain identifier (e.g., "ethereum", "bsc")
            count: Number of units to deposit (each is 1x denomination)
            private_key: Wallet private key for the transaction

        Returns:
            Dict with success, tx_hash, count, and notes (list of secret+leaf_index pairs)
        """
        asset = self.registry.get_asset(symbol, chain)
        if not asset:
            return {"success": False, "error": f"Asset {symbol} not found on chain {chain}"}

        if count < 1 or count > 20:
            return {"success": False, "error": f"Batch size must be 1-20, got {count}"}

        # Generate N independent secrets
        secrets_list = [self.generate_secret() for _ in range(count)]
        leaf_hashes = [self.compute_leaf_hash(s) for s in secrets_list]

        adapter = self._get_adapter(asset)
        result = adapter.batch_deposit(leaf_hashes, private_key)

        if not result.success:
            return {"success": False, "error": result.error}

        return {
            "success": True,
            "tx_hash": result.tx_hash,
            "count": count,
            "notes": [
                {"secret": hex(s), "leaf_index": idx}
                for s, idx in zip(secrets_list, result.leaf_indices)
            ],
            "new_root": result.new_root,
        }

    def withdraw(self, symbol: str, chain: str, secret: int,
                 leaf_index: int, recipient: str,
                 private_key: str) -> WithdrawResult:
        """
        Withdraw funds from the mixer using a zkSNARK proof.

        Args:
            symbol: Asset symbol
            chain: Chain identifier
            secret: The spending secret (same one used during deposit)
            leaf_index: The leaf index from the deposit
            recipient: Destination address for the withdrawal
            private_key: Wallet private key

        Returns:
            WithdrawResult with transaction details
        """
        asset = self.registry.get_asset(symbol, chain)
        if not asset:
            return WithdrawResult(
                success=False, tx_hash="", nullifier="", recipient=recipient,
                chain=chain, asset=symbol, amount="0",
                error=f"Asset {symbol} not found on chain {chain}"
            )

        adapter = self._get_adapter(asset)

        # Get current Merkle tree state from chain
        root = adapter.get_root()
        path, address_bits = adapter.get_path(leaf_index)

        # Compute external hash
        ext_hash = adapter.get_ext_hash(
            asset.mixer_contract or "", recipient
        )

        # Generate zkSNARK proof (using shared C++ prover)
        proof_data = self._generate_proof(
            root=root,
            secret=secret,
            ext_hash=ext_hash,
            address_bits=address_bits,
            path=path,
            leaf_index=leaf_index,
        )

        if proof_data is None:
            return WithdrawResult(
                success=False, tx_hash="", nullifier="", recipient=recipient,
                chain=chain, asset=symbol, amount="0",
                error="Proof generation failed"
            )

        return adapter.withdraw(proof_data, recipient, private_key)

    def batch_withdraw(self, symbol: str, chain: str,
                       notes: List[Tuple[int, int]], recipient: str,
                       private_key: str) -> BatchWithdrawResult:
        """
        Batch withdraw — withdraw multiple notes in a single transaction.

        Args:
            symbol: Asset symbol (e.g., "ETH", "USDT")
            chain: Chain identifier (e.g., "ethereum", "bsc")
            notes: List of (secret, leaf_index) pairs to withdraw
            recipient: Destination address for all withdrawals
            private_key: Wallet private key

        Returns:
            BatchWithdrawResult with transaction details and all nullifiers
        """
        asset = self.registry.get_asset(symbol, chain)
        if not asset:
            return BatchWithdrawResult(
                success=False, tx_hash="", nullifiers=[], recipient=recipient,
                chain=chain, asset=symbol, total_amount="0", count=0,
                error=f"Asset {symbol} not found on chain {chain}"
            )

        if len(notes) < 1 or len(notes) > 20:
            return BatchWithdrawResult(
                success=False, tx_hash="", nullifiers=[], recipient=recipient,
                chain=chain, asset=symbol, total_amount="0", count=0,
                error=f"Batch size must be 1-20, got {len(notes)}"
            )

        adapter = self._get_adapter(asset)

        # Generate proofs for each note
        proofs = []
        for secret, leaf_index in notes:
            root = adapter.get_root()
            path, address_bits = adapter.get_path(leaf_index)
            ext_hash = adapter.get_ext_hash(
                asset.mixer_contract or "", recipient
            )

            proof_data = self._generate_proof(
                root=root,
                secret=secret,
                ext_hash=ext_hash,
                address_bits=address_bits,
                path=path,
                leaf_index=leaf_index,
            )

            if proof_data is None:
                return BatchWithdrawResult(
                    success=False, tx_hash="", nullifiers=[],
                    recipient=recipient, chain=chain, asset=symbol,
                    total_amount="0", count=0,
                    error=f"Proof generation failed for leaf_index={leaf_index}"
                )
            proofs.append(proof_data)

        return adapter.batch_withdraw(proofs, recipient, private_key)

    def check_deposit_status(self, symbol: str, chain: str,
                              nullifier: int) -> dict:
        """Check if a specific nullifier has been spent"""
        asset = self.registry.get_asset(symbol, chain)
        if not asset:
            return {"error": f"Asset {symbol} not found on {chain}"}

        adapter = self._get_adapter(asset)
        is_spent = adapter.is_spent(nullifier)

        return {
            "nullifier": str(nullifier),
            "is_spent": is_spent,
            "chain": chain,
            "asset": symbol,
        }

    def get_pool_info(self, symbol: str, chain: str) -> dict:
        """Get information about a mixer pool"""
        asset = self.registry.get_asset(symbol, chain)
        if not asset:
            return {"error": f"Asset {symbol} not found on {chain}"}

        try:
            adapter = self._get_adapter(asset)
            return {
                "chain": chain,
                "asset": symbol,
                "denomination": asset.denomination,
                "decimals": asset.decimals,
                "current_root": str(adapter.get_root()),
                "pool_balance": str(adapter.get_balance()),
                "chain_type": asset.chain_type.value,
            }
        except Exception as e:
            return {"error": str(e)}

    # =========================================================================
    #                       INTERNAL METHODS
    # =========================================================================

    def _get_adapter(self, asset: AssetConfig) -> ChainAdapter:
        """Get or create a chain adapter for the given asset"""
        key = f"{asset.symbol}@{asset.chain}"
        if key not in self._adapters:
            adapter = get_adapter_for_asset(asset)
            adapter.connect()
            self._adapters[key] = adapter
        return self._adapters[key]

    def _get_prover(self):
        """Lazy-load the native zkSNARK prover"""
        if self._prover is None and self.native_lib_path:
            try:
                # Import the original miximus Python wrapper
                base = os.path.join(os.path.dirname(__file__), '..', 'ethsnarks-miximus')
                miximus_path = os.path.join(base, 'python')
                ethsnarks_path = os.path.join(base, 'ethsnarks')  # parent of ethsnarks pkg
                sys.path.insert(0, miximus_path)
                if ethsnarks_path not in sys.path:
                    sys.path.insert(0, ethsnarks_path)
                from miximus import Miximus
                self._prover = Miximus(
                    self.native_lib_path,
                    self.vk_file,
                    self.pk_file,
                )
            except Exception as e:
                import traceback
                print(f"ERROR: Could not load native prover: {e}", flush=True)
                traceback.print_exc()
                self._prover = None
        return self._prover

    def _generate_proof(self, root: int, secret: int, ext_hash: int,
                        address_bits: List[bool], path: List[int],
                        leaf_index: int) -> Optional[ProofData]:
        """Generate a zkSNARK proof using the native C++ library"""
        prover = self._get_prover()
        if prover is None:
            print("ERROR: Prover not available — proof generation skipped", flush=True)
            return None

        try:
            # Convert bool list from Solidity to int list for the C++ prover
            address_bits_int = [int(b) for b in address_bits]
            print(f"DEBUG address_bits type={type(address_bits)}, len={len(address_bits)}, first5={address_bits[:5]}", flush=True)
            print(f"DEBUG address_bits_int len={len(address_bits_int)}, first5={address_bits_int[:5]}", flush=True)
            print(f"DEBUG path len={len(path)}", flush=True)
            print(f"Generating proof: root={hex(root)[:16]}..., leaf_index={leaf_index}", flush=True)
            proof = prover.prove(
                root=root,
                spend_preimage=secret,
                exthash=ext_hash,
                address_bits=address_bits_int,
                path=path,
            )

            nullifier = self.compute_nullifier(secret, leaf_index)

            # Extract 8 proof points from the Proof object's JSON
            # ethsnarks format: A=[x,y], B=[[X.c1,X.c0],[Y.c1,Y.c0]], C=[x,y]
            # Pass G2 coordinates directly — do NOT swap (matches EVM precompile)
            proof_dict = json.loads(proof.to_json())
            proof_points = [
                int(proof_dict['A'][0], 16),
                int(proof_dict['A'][1], 16),
                int(proof_dict['B'][0][0], 16),
                int(proof_dict['B'][0][1], 16),
                int(proof_dict['B'][1][0], 16),
                int(proof_dict['B'][1][1], 16),
                int(proof_dict['C'][0], 16),
                int(proof_dict['C'][1], 16),
            ]

            print(f"Proof generated successfully!", flush=True)
            return ProofData(
                root=root,
                nullifier=nullifier,
                proof_json=proof.to_json(),
                proof_points=proof_points,
                external_hash=ext_hash,
            )
        except Exception as e:
            import traceback
            print(f"ERROR: Proof generation failed: {e}", flush=True)
            traceback.print_exc()
            return None

    @staticmethod
    def _python_mimc(data: List[int]) -> int:
        """
        Pure Python MiMC hash using ethsnarks (Keccak-256 round constants).
        Matches the C++ circuit exactly.
        """
        from ethsnarks.mimc import mimc_hash
        return mimc_hash(data)


# =========================================================================
#                          CLI INTERFACE
# =========================================================================

def main():
    """Command-line interface for the multi-chain mixer"""
    import argparse

    parser = argparse.ArgumentParser(description="Miximus Multi-Chain Mixer")
    subparsers = parser.add_subparsers(dest="command")

    # List assets
    list_parser = subparsers.add_parser("list", help="List supported assets")
    list_parser.add_argument("--chain", help="Filter by chain")
    list_parser.add_argument("--symbol", help="Filter by symbol")

    # List chains
    subparsers.add_parser("chains", help="List supported blockchains")

    # Info
    info_parser = subparsers.add_parser("info", help="Asset info")
    info_parser.add_argument("symbol", help="Asset symbol (e.g., ETH)")
    info_parser.add_argument("chain", help="Chain (e.g., ethereum)")

    # Batch deposit
    batch_parser = subparsers.add_parser("batch-deposit", help="Batch deposit N units")
    batch_parser.add_argument("symbol", help="Asset symbol (e.g., ETH)")
    batch_parser.add_argument("chain", help="Chain (e.g., ethereum)")
    batch_parser.add_argument("count", type=int, help="Number of units to deposit (1-20)")
    batch_parser.add_argument("--key", required=True, help="Wallet private key")

    # Batch withdraw
    bw_parser = subparsers.add_parser("batch-withdraw", help="Batch withdraw N notes")
    bw_parser.add_argument("symbol", help="Asset symbol (e.g., ETH)")
    bw_parser.add_argument("chain", help="Chain (e.g., ethereum)")
    bw_parser.add_argument("recipient", help="Destination address")
    bw_parser.add_argument("--notes", required=True, nargs="+",
                           help="Notes as secret:leaf_index pairs (e.g., 0xabc:5 0xdef:8)")
    bw_parser.add_argument("--key", required=True, help="Wallet private key")

    # Summary
    subparsers.add_parser("summary", help="Registry summary")

    args = parser.parse_args()
    mixer = MiximusMultiChain()

    if args.command == "list":
        assets = mixer.list_supported_assets()
        if args.chain:
            assets = [a for a in assets if a["chain"] == args.chain]
        if args.symbol:
            assets = [a for a in assets if a["symbol"] == args.symbol]

        print(f"\n{'Symbol':<12} {'Name':<25} {'Chain':<18} {'Type':<10} {'Decimals':<10}")
        print("-" * 85)
        for a in sorted(assets, key=lambda x: (x["chain"], x["symbol"])):
            print(f"{a['symbol']:<12} {a['name']:<25} {a['chain']:<18} {a['type']:<10} {a['decimals']:<10}")
        print(f"\nTotal: {len(assets)} assets")

    elif args.command == "chains":
        chains = mixer.list_chains()
        print(f"\n{'ID':<20} {'Name':<25} {'Type':<12} {'Native':<8}")
        print("-" * 65)
        for c in sorted(chains, key=lambda x: x["id"]):
            print(f"{c['id']:<20} {c['name']:<25} {c['type']:<12} {c['native_currency']:<8}")
        print(f"\nTotal: {len(chains)} chains")

    elif args.command == "batch-deposit":
        result = mixer.batch_deposit(args.symbol, args.chain, args.count, args.key)
        if result["success"]:
            print(f"\nBatch deposit successful! TX: {result['tx_hash']}")
            print(f"Deposited {result['count']} x {args.symbol} on {args.chain}")
            print(f"\nSAVE THESE SECRETS (each withdraws 1 unit):")
            for i, note in enumerate(result["notes"]):
                print(f"  [{i+1}] secret={note['secret']}  leaf_index={note['leaf_index']}")
        else:
            print(f"\nBatch deposit failed: {result.get('error')}")

    elif args.command == "batch-withdraw":
        # Parse notes from "secret:leaf_index" format
        notes = []
        for note_str in args.notes:
            parts = note_str.split(":")
            if len(parts) != 2:
                print(f"Invalid note format: {note_str} (expected secret:leaf_index)")
                sys.exit(1)
            secret = int(parts[0], 0)  # auto-detect hex/decimal
            leaf_index = int(parts[1])
            notes.append((secret, leaf_index))

        result = mixer.batch_withdraw(args.symbol, args.chain, notes,
                                      args.recipient, args.key)
        if result.success:
            print(f"\nBatch withdraw successful! TX: {result.tx_hash}")
            print(f"Withdrew {result.count} x {args.symbol} on {args.chain}")
            print(f"Recipient: {result.recipient}")
            print(f"Total amount: {result.total_amount}")
            print(f"\nNullifiers spent:")
            for i, n in enumerate(result.nullifiers):
                print(f"  [{i+1}] {n}")
        else:
            print(f"\nBatch withdraw failed: {result.error}")

    elif args.command == "info":
        info = mixer.get_asset_info(args.symbol, args.chain)
        if info:
            print(json.dumps(info, indent=2))
        else:
            print(f"Asset {args.symbol} not found on chain {args.chain}")

    elif args.command == "summary":
        summary = mixer.registry.summary()
        print(json.dumps(summary, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
