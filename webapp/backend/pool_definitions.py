"""
Canonical pool definitions for the Miximus custodial service.

Imported by both seed_pools.py (CLI seeding) and routes/admin.py (API init-pools),
so it must NOT import from app, models, or any Flask module.
"""

import os


EVM_CHAINS = frozenset({
    'ethereum', 'polygon', 'bsc', 'arbitrum', 'optimism',
    'avalanche', 'fantom', 'base', 'gnosis', 'cronos', 'zkevm', 'linea',
})


def _derive_evm_address(private_key_hex: str) -> str:
    pk = private_key_hex.strip().lstrip('0x')
    if not pk:
        return ''
    try:
        from eth_account import Account
        return Account.from_key('0x' + pk).address
    except Exception as e:
        return f'<EVM error: {e}>'


def _derive_btc_address(private_key_hex: str, testnet: bool = False) -> str:
    pk = private_key_hex.strip().lstrip('0x')
    if not pk:
        return ''
    try:
        if testnet:
            from bit import PrivateKeyTestnet
            return PrivateKeyTestnet.from_hex(pk).address
        else:
            from bit import Key
            return Key.from_hex(pk).address
    except Exception as e:
        return f'<BTC error: {e}>'


def _derive_tron_address(private_key_hex: str) -> str:
    pk = private_key_hex.strip().lstrip('0x')
    if not pk:
        return ''
    try:
        from tronpy.keys import PrivateKey
        return PrivateKey(bytes.fromhex(pk)).public_key.to_base58check_address()
    except Exception as e:
        return f'<TRON error: {e}>'


def derive_all_addresses(private_key_hex: str) -> dict:
    """Return all derived wallet addresses from a single private key."""
    return {
        'evm_address':         _derive_evm_address(private_key_hex),
        'btc_mainnet_address': _derive_btc_address(private_key_hex, testnet=False),
        'btc_testnet_address': _derive_btc_address(private_key_hex, testnet=True),
        'tron_address':        _derive_tron_address(private_key_hex),
    }


def get_pool_definitions(private_key_hex: str = '') -> list:
    """
    Return the list of canonical pool definitions, with wallet addresses
    derived from private_key_hex (or env var SERVICE_WALLET_PRIVATE_KEY
    if private_key_hex is not given).
    """
    pk = private_key_hex or os.getenv('SERVICE_WALLET_PRIVATE_KEY', '')
    addrs = derive_all_addresses(pk)

    evm   = addrs['evm_address']   or '<SET SERVICE_WALLET_PRIVATE_KEY>'
    btc_m = addrs['btc_mainnet_address'] or '<SET SERVICE_WALLET_PRIVATE_KEY>'
    btc_t = addrs['btc_testnet_address'] or '<SET SERVICE_WALLET_PRIVATE_KEY>'
    tron  = addrs['tron_address']  or '<SET SERVICE_WALLET_PRIVATE_KEY>'

    return [
        # ─── Sepolia Testnet (EVM) ────────────────────────────────────────
        {
            'symbol': 'ETH',
            'chain': 'ethereum',
            'network_mode': 'testnet',
            'mixer_contract': '0x85A4ecCe24580f6d90adFFD74d9B061BD3A4f3c4',
            'denomination': '60000000000000000',   # 0.06 ETH
            'commission_rate': 0.03,
            'min_confirmations': 3,
            'service_wallet_address': evm,
        },
        {
            'symbol': 'USDC',
            'chain': 'ethereum',
            'network_mode': 'testnet',
            'mixer_contract': '0xBeB1B7eA73e18fA7E588f09C9154F2781E48578b',
            'denomination': '1000000',   # 1 USDC
            'commission_rate': 0.03,
            'min_confirmations': 3,
            'service_wallet_address': evm,
        },

        # ─── Tron Nile Testnet ────────────────────────────────────────────
        {
            'symbol': 'USDT',
            'chain': 'tron',
            'network_mode': 'testnet',
            'mixer_contract': 'TTPPEKMUWjATr4kQrfUz9ombiC7oHofnVB',
            'denomination': '1000000',   # 1 USDT
            'commission_rate': 0.03,
            'min_confirmations': 19,
            'service_wallet_address': tron,
        },

        # ─── Bitcoin Testnet (custodial) ──────────────────────────────────
        {
            'symbol': 'BTC',
            'chain': 'bitcoin',
            'network_mode': 'testnet',
            'mixer_contract': 'custodial',
            'denomination': '1000000',   # 0.01 BTC ≈ $950
            'commission_rate': 0.03,
            'min_confirmations': 3,
            'service_wallet_address': btc_t,
        },

        # ─── BTC Privacy Anchor (Sepolia) ─────────────────────────────────
        {
            'symbol': 'BTC_ANCHOR',
            'chain': 'ethereum',
            'network_mode': 'testnet',
            'mixer_contract': '0xBF36B99a836d7b08FaDAc880a35046C154Ceb993',
            'denomination': '1',   # 1 wei
            'commission_rate': 0.0,
            'min_confirmations': 1,
            'service_wallet_address': evm,
        },

        # ─── Bitcoin Mainnet (custodial) ──────────────────────────────────
        {
            'symbol': 'BTC',
            'chain': 'bitcoin',
            'network_mode': 'mainnet',
            'mixer_contract': 'custodial',
            'denomination': '1000000',   # 0.01 BTC ≈ $950
            'commission_rate': 0.03,
            'min_confirmations': 3,
            'service_wallet_address': btc_m,
        },

        # ─── BTC Privacy Anchor (Polygon Mainnet) ─────────────────────────
        {
            'symbol': 'BTC_ANCHOR',
            'chain': 'polygon',
            'network_mode': 'mainnet',
            'mixer_contract': '0x85A4ecCe24580f6d90adFFD74d9B061BD3A4f3c4',
            'denomination': '1',   # 1 wei MATIC
            'commission_rate': 0.0,
            'min_confirmations': 1,
            'service_wallet_address': evm,
        },
    ]
