"""
Pool info routes — no auth required.
"""

from flask import Blueprint, request, jsonify
from mixer_service import get_mixer

pool_bp = Blueprint('pool', __name__)


@pool_bp.route('/pool/<symbol>/<chain>', methods=['GET'])
def pool_info(symbol, chain):
    """Get mixer pool info for a specific asset on a specific chain."""
    network_mode = request.args.get('network_mode', 'mainnet')
    if network_mode not in ('mainnet', 'testnet'):
        return jsonify({'error': 'Invalid network_mode'}), 400

    try:
        mixer = get_mixer(network_mode)
        info = mixer.get_pool_info(symbol, chain)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if not info:
        return jsonify({'error': 'Pool not found'}), 404

    return jsonify(info)
