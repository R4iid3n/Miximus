"""
Asset Registry — Maps every supported asset to its chain adapter configuration.

This is the central routing table. Given any asset (symbol + chain),
it returns the correct adapter type, contract address, and parameters.
"""

import json
import os
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from .base import ChainType


@dataclass
class AssetConfig:
    """Configuration for a single mixable asset"""
    symbol: str
    name: str
    chain: str
    chain_type: ChainType
    asset_type: str  # "native", "erc20", "bep20", "trc20", etc.
    decimals: int
    denomination: int
    contract_address: Optional[str] = None  # Token contract (for tokens)
    mixer_contract: Optional[str] = None  # Deployed mixer contract
    rpc_url: Optional[str] = None


class AssetRegistry:
    """
    Central registry of all supported assets across all chains.

    Usage:
        registry = AssetRegistry.load()
        asset = registry.get_asset("USDT", "ethereum")
        adapter = registry.get_adapter(asset)
    """

    def __init__(self):
        self.assets: Dict[str, AssetConfig] = {}
        self.chains: Dict[str, dict] = {}

    @classmethod
    def load(cls, config_path: str = None) -> 'AssetRegistry':
        """Load the asset registry from the JSON configuration file"""
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__), '..', '..', 'config', 'assets.json'
            )

        registry = cls()

        with open(config_path, 'r') as f:
            config = json.load(f)

        # Load chain configurations
        for chain_id, chain_config in config.get('chains', {}).items():
            registry.chains[chain_id] = chain_config

        # Load all asset categories
        for category in ['native_coins', 'stablecoins', 'wrapped_assets',
                         'defi_tokens', 'exchange_network_tokens']:
            for asset_data in config.get('assets', {}).get(category, []):
                chain_id = asset_data['chain']
                chain_config = registry.chains.get(chain_id, {})

                chain_type_str = chain_config.get('type', 'unknown')
                try:
                    chain_type = ChainType(chain_type_str)
                except ValueError:
                    chain_type = ChainType.EVM  # Default

                asset = AssetConfig(
                    symbol=asset_data['symbol'],
                    name=asset_data['name'],
                    chain=chain_id,
                    chain_type=chain_type,
                    asset_type=asset_data['type'],
                    decimals=asset_data['decimals'],
                    denomination=int(asset_data['denomination']),
                    contract_address=asset_data.get('contract'),
                    rpc_url=chain_config.get('rpc_url'),
                )

                # Create unique key: symbol@chain
                key = f"{asset.symbol}@{asset.chain}"
                registry.assets[key] = asset

        return registry

    def get_asset(self, symbol: str, chain: str) -> Optional[AssetConfig]:
        """Get asset configuration by symbol and chain"""
        key = f"{symbol}@{chain}"
        return self.assets.get(key)

    def get_assets_by_symbol(self, symbol: str) -> List[AssetConfig]:
        """Get all instances of an asset across chains"""
        return [a for a in self.assets.values() if a.symbol == symbol]

    def get_assets_by_chain(self, chain: str) -> List[AssetConfig]:
        """Get all assets on a specific chain"""
        return [a for a in self.assets.values() if a.chain == chain]

    def get_all_assets(self) -> List[AssetConfig]:
        """Get all registered assets"""
        return list(self.assets.values())

    def get_evm_assets(self) -> List[AssetConfig]:
        """Get all EVM-chain assets (deployable with Solidity contracts)"""
        return [a for a in self.assets.values() if a.chain_type == ChainType.EVM]

    def get_native_assets(self) -> List[AssetConfig]:
        """Get all native currency assets"""
        return [a for a in self.assets.values() if a.asset_type == 'native']

    def get_token_assets(self) -> List[AssetConfig]:
        """Get all token assets (ERC20, BEP20, TRC20, etc.)"""
        return [a for a in self.assets.values()
                if a.asset_type in ('erc20', 'bep20', 'trc20')]

    def summary(self) -> dict:
        """Return a summary of the registry"""
        chains_used = set(a.chain for a in self.assets.values())
        chain_types = set(a.chain_type.value for a in self.assets.values())
        return {
            "total_assets": len(self.assets),
            "total_chains": len(chains_used),
            "chain_types": sorted(chain_types),
            "chains": sorted(chains_used),
            "by_type": {
                "native": len(self.get_native_assets()),
                "tokens": len(self.get_token_assets()),
                "evm": len(self.get_evm_assets()),
            }
        }


def get_adapter_for_asset(asset: AssetConfig, **kwargs):
    """
    Factory function: given an asset config, return the appropriate chain adapter.

    This is the main routing function that connects the asset registry
    to the chain-specific adapter implementations.
    """
    from .evm import EVMAdapter

    if asset.chain_type == ChainType.EVM:
        is_token = asset.asset_type in ('erc20', 'bep20')
        return EVMAdapter(
            chain_id=asset.chain,
            rpc_url=asset.rpc_url or "",
            native_symbol=asset.symbol,
            denomination=asset.denomination,
            contract_address=asset.mixer_contract or "",
            is_token=is_token,
            token_address=asset.contract_address if is_token else None,
            **kwargs,
        )

    elif asset.chain_type == ChainType.TVM:
        # Tron adapter (uses tronpy)
        raise NotImplementedError(
            f"Tron adapter for {asset.symbol}: deploy MiximusNativeTron.sol "
            f"or MiximusTRC20.sol via TronBox"
        )

    elif asset.chain_type == ChainType.SVM:
        # Solana adapter (uses solana-py)
        raise NotImplementedError(
            f"Solana adapter for {asset.symbol}: deploy via Anchor CLI"
        )

    elif asset.chain_type == ChainType.UTXO:
        # UTXO chains use HTLC adapter
        raise NotImplementedError(
            f"UTXO adapter for {asset.symbol}: use MiximusHTLC from "
            f"contracts/utxo/miximus_htlc.py"
        )

    elif asset.chain_type in (ChainType.COSMOS,):
        raise NotImplementedError(
            f"Cosmos adapter for {asset.symbol}: deploy CosmWasm contract "
            f"from contracts/cosmos/src/miximus.rs"
        )

    elif asset.chain_type == ChainType.NEAR:
        raise NotImplementedError(
            f"NEAR adapter for {asset.symbol}: deploy contract from "
            f"contracts/near/src/lib.rs"
        )

    elif asset.chain_type == ChainType.CARDANO:
        raise NotImplementedError(
            f"Cardano adapter for {asset.symbol}: deploy Plutus validator from "
            f"contracts/cardano/MiximusCardano.hs"
        )

    elif asset.chain_type == ChainType.SUBSTRATE:
        raise NotImplementedError(
            f"Polkadot adapter for {asset.symbol}: deploy ink! contract from "
            f"contracts/polkadot/lib.rs"
        )

    elif asset.chain_type == ChainType.ALGORAND:
        raise NotImplementedError(
            f"Algorand adapter for {asset.symbol}: deploy PyTeal contract from "
            f"contracts/algorand/miximus_algorand.py"
        )

    elif asset.chain_type == ChainType.TEZOS:
        raise NotImplementedError(
            f"Tezos adapter for {asset.symbol}: deploy SmartPy contract from "
            f"contracts/tezos/miximus_tezos.py"
        )

    elif asset.chain_type == ChainType.TON:
        raise NotImplementedError(
            f"TON adapter for {asset.symbol}: deploy FunC contract from "
            f"contracts/ton/miximus_ton.fc"
        )

    elif asset.chain_type == ChainType.STELLAR:
        raise NotImplementedError(
            f"Stellar adapter for {asset.symbol}: deploy Soroban contract from "
            f"contracts/stellar/src/lib.rs"
        )

    elif asset.chain_type == ChainType.WAVES:
        raise NotImplementedError(
            f"Waves adapter for {asset.symbol}: deploy Ride contract from "
            f"contracts/waves/miximus_waves.ride"
        )

    elif asset.chain_type == ChainType.XRPL:
        raise NotImplementedError(
            f"XRP adapter for {asset.symbol}: use escrow-based mixer from "
            f"contracts/ripple/miximus_xrpl.py"
        )

    else:
        raise NotImplementedError(
            f"No adapter available for chain type: {asset.chain_type.value}"
        )
