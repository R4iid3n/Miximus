"""
Asset listing routes — returns all supported currencies.
"""

from flask import Blueprint, request, jsonify
from mixer_service import get_mixer

assets_bp = Blueprint('assets', __name__)


@assets_bp.route('/assets', methods=['GET'])
def list_assets():
    """List all supported assets for the given network mode."""
    network_mode = request.args.get('network_mode', 'mainnet')
    if network_mode not in ('mainnet', 'testnet'):
        return jsonify({'error': 'Invalid network_mode'}), 400

    try:
        mixer = get_mixer(network_mode)
        assets = mixer.list_supported_assets()
        chains = mixer.list_chains()
    except Exception as e:
        return jsonify({'error': f'Failed to load assets: {str(e)}'}), 500

    return jsonify({
        'assets': assets,
        'chains': chains,
        'network_mode': network_mode,
    })


@assets_bp.route('/assets/<symbol>/<chain>', methods=['GET'])
def get_asset(symbol, chain):
    """Get details for a specific asset on a specific chain."""
    network_mode = request.args.get('network_mode', 'mainnet')
    if network_mode not in ('mainnet', 'testnet'):
        return jsonify({'error': 'Invalid network_mode'}), 400

    try:
        mixer = get_mixer(network_mode)
        info = mixer.get_asset_info(symbol, chain)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if not info:
        return jsonify({'error': 'Asset not found'}), 404

    return jsonify(info)
