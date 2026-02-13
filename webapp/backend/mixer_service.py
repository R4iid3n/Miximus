"""
Bridge between Flask routes and the existing MiximusMultiChain orchestrator.
Caches one mixer instance per network mode (testnet/mainnet).
"""

import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'python'))

from miximus_multichain import MiximusMultiChain

_mixers = {}


def get_mixer(network_mode='mainnet'):
    """Get or create a MiximusMultiChain instance for the given network mode."""
    if network_mode not in _mixers:
        if network_mode == 'testnet':
            config_path = os.path.join(PROJECT_ROOT, 'config', 'assets_testnet.json')
        else:
            config_path = os.path.join(PROJECT_ROOT, 'config', 'assets.json')

        _mixers[network_mode] = MiximusMultiChain(
            config_path=config_path,
            native_lib_path=os.path.join(PROJECT_ROOT, 'ethsnarks-miximus', '.build', 'libmiximus.so'),
            pk_file=os.path.join(PROJECT_ROOT, 'ethsnarks-miximus', '.keys', 'miximus.pk.raw'),
            vk_file=os.path.join(PROJECT_ROOT, 'ethsnarks-miximus', '.keys', 'miximus.vk.json'),
        )
    return _mixers[network_mode]


def clear_cache():
    """Clear cached mixer instances (for testing)."""
    _mixers.clear()
