"""
Flask configuration for Miximus custodial mixing service.
"""

import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


class BaseConfig:
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Prover paths
    PROVER_LIB_PATH = os.environ.get('PROVER_LIB_PATH',
        os.path.join(PROJECT_ROOT, 'ethsnarks-miximus', '.build', 'libmiximus.so'))
    PK_FILE = os.environ.get('PK_FILE',
        os.path.join(PROJECT_ROOT, 'ethsnarks-miximus', '.keys', 'miximus.pk.raw'))
    VK_FILE = os.environ.get('VK_FILE',
        os.path.join(PROJECT_ROOT, 'ethsnarks-miximus', '.keys', 'miximus.vk.json'))

    # Asset config paths
    MAINNET_CONFIG = os.path.join(PROJECT_ROOT, 'config', 'assets.json')
    TESTNET_CONFIG = os.path.join(PROJECT_ROOT, 'config', 'assets_testnet.json')

    # Custodial service settings
    SERVICE_WALLET_PRIVATE_KEY = os.environ.get('SERVICE_WALLET_PRIVATE_KEY', '')
    COMMISSION_RATE = float(os.environ.get('COMMISSION_RATE', '0.03'))  # 3%
    ORDER_EXPIRY_SECONDS = int(os.environ.get('ORDER_EXPIRY_SECONDS', '7200'))  # 2 hours
    MIN_CONFIRMATIONS = int(os.environ.get('MIN_CONFIRMATIONS', '3'))
    MIN_PROCESSING_DELAY = int(os.environ.get('MIN_PROCESSING_DELAY', '30'))
    MAX_PROCESSING_DELAY = int(os.environ.get('MAX_PROCESSING_DELAY', '300'))

    # External fee wallets — mainnet (fees forwarded here after each withdrawal)
    FEE_WALLET_EVM = os.environ.get('FEE_WALLET_EVM', '')
    FEE_WALLET_TRON = os.environ.get('FEE_WALLET_TRON', '')
    FEE_WALLET_BTC = os.environ.get('FEE_WALLET_BTC', '')
    # External fee wallets — testnet
    FEE_WALLET_EVM_TESTNET = os.environ.get('FEE_WALLET_EVM_TESTNET', '')
    FEE_WALLET_TRON_TESTNET = os.environ.get('FEE_WALLET_TRON_TESTNET', '')
    FEE_WALLET_BTC_TESTNET = os.environ.get('FEE_WALLET_BTC_TESTNET', '')

    # Admin dashboard credentials
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
    SECRET_KEY = os.environ.get('SECRET_KEY', '')


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(
        os.path.dirname(__file__), 'miximus_dev.db')


class ProductionConfig(BaseConfig):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'postgresql://localhost/miximus')
