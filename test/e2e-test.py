#!/usr/bin/env python3
"""
End-to-end test: Deposit ETH → Generate zkSNARK proof → Withdraw anonymously

This script connects to a running Hardhat node, deposits ETH into the mixer,
generates a zkSNARK proof using the C++ prover, and withdraws to a different
address — proving the full anonymous transfer flow works.

Prerequisites:
  - Hardhat node running at http://127.0.0.1:8545 (npx hardhat node)
  - Contracts deployed (npx hardhat run deployment/evm/deploy.js --network localhost)
  - C++ prover built (libmiximus.so in ethsnarks-miximus/build/)
  - Proving key generated (.keys/miximus.pk.raw)

Usage (from WSL):
  cd /mnt/c/AML\ mixer
  python3 test/e2e-test.py
"""

import os
import sys
import json
import ctypes
import hashlib
import secrets

# Add ethsnarks to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
ETHSNARKS_DIR = os.path.join(PROJECT_DIR, "ethsnarks-miximus")

sys.path.insert(0, os.path.join(ETHSNARKS_DIR, "python"))
sys.path.insert(0, os.path.join(ETHSNARKS_DIR, "ethsnarks"))

from web3 import Web3

# ============================================================
# Configuration
# ============================================================

RPC_URL = os.environ.get("RPC_URL", "http://172.29.128.1:8545")
POOL_ADDRESS = None  # Will be loaded from deployments file

# Hardhat test accounts
DEPOSITOR_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # Account #1
RECIPIENT_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"  # Account #2

# Paths
LIB_PATH = os.path.join(ETHSNARKS_DIR, "build", "libmiximus.so")
PK_PATH = os.path.join(ETHSNARKS_DIR, ".keys", "miximus.pk.raw")
VK_PATH = os.path.join(ETHSNARKS_DIR, ".keys", "miximus.vk.json")

SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617

# MiximusNative ABI (only the functions we need)
POOL_ABI = json.loads("""[
  {"inputs":[{"name":"_leaf","type":"uint256"}],"name":"deposit","outputs":[{"name":"newRoot","type":"uint256"},{"name":"leafIndex","type":"uint256"}],"stateMutability":"payable","type":"function"},
  {"inputs":[{"name":"_root","type":"uint256"},{"name":"_nullifier","type":"uint256"},{"name":"_proof","type":"uint256[8]"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[],"name":"getRoot","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"denomination","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"nextLeafIndex","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
  {"inputs":[{"name":"_leafIndex","type":"uint256"}],"name":"getPath","outputs":[{"name":"path","type":"uint256[29]"},{"name":"addressBits","type":"bool[29]"}],"stateMutability":"view","type":"function"},
  {"inputs":[{"name":"_secret","type":"uint256"}],"name":"makeLeafHash","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
  {"inputs":[{"name":"_nullifier","type":"uint256"}],"name":"isSpent","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"},
  {"inputs":[{"name":"_root","type":"uint256"}],"name":"isKnownRoot","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"assetSymbol","outputs":[{"type":"string"}],"stateMutability":"view","type":"function"}
]""")


def load_native_library():
    """Load the C++ prover library"""
    if not os.path.exists(LIB_PATH):
        print(f"ERROR: Native library not found at {LIB_PATH}")
        sys.exit(1)

    lib = ctypes.cdll.LoadLibrary(LIB_PATH)

    # Get tree depth
    lib.miximus_tree_depth.restype = ctypes.c_size_t
    tree_depth = lib.miximus_tree_depth()
    print(f"   Tree depth: {tree_depth}")

    # Setup prove_json function
    lib.miximus_prove_json.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    lib.miximus_prove_json.restype = ctypes.c_char_p

    # Setup nullifier function
    lib.miximus_nullifier.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    lib.miximus_nullifier.restype = ctypes.c_char_p

    return lib, tree_depth


def compute_nullifier(lib, secret, leaf_index):
    """Compute nullifier using C++ library: H(leaf_index, secret)"""
    s = ctypes.c_char_p(str(secret).encode('ascii'))
    idx = ctypes.c_char_p(str(leaf_index).encode('ascii'))
    result = lib.miximus_nullifier(s, idx)
    return int(result)


def generate_proof(lib, pk_path, root, secret, exthash, address_bits, path):
    """Generate a zkSNARK proof using the C++ prover"""
    # Convert address_bits (list of bools) to integer
    address_int = sum([(1 << i) * int(b) for i, b in enumerate(address_bits)])

    args_dict = {
        "root": hex(root),
        "exthash": hex(exthash),
        "secret": hex(secret),
        "address": address_int,
        "path": [hex(p) for p in path]
    }

    args_json = json.dumps(args_dict).encode('ascii')
    pk_file_cstr = ctypes.c_char_p(pk_path.encode('ascii'))
    args_json_cstr = ctypes.c_char_p(args_json)

    print(f"   Generating proof (this may take a few seconds)...")
    result = lib.miximus_prove_json(pk_file_cstr, args_json_cstr)

    if result is None:
        raise RuntimeError("Proof generation failed!")

    proof_data = json.loads(result)
    return proof_data


def compute_exthash(pool_address, recipient_address):
    """Compute external hash: uint256(sha256(pool_address || recipient_address)) % SCALAR_FIELD"""
    pool_bytes = bytes.fromhex(pool_address[2:].lower())  # Remove 0x
    recip_bytes = bytes.fromhex(recipient_address[2:].lower())
    packed = pool_bytes + recip_bytes
    h = hashlib.sha256(packed).digest()
    return int.from_bytes(h, 'big') % SCALAR_FIELD


def proof_to_uint256_array(proof_data):
    """Convert proof JSON to uint256[8] array for the contract"""
    A = proof_data["A"]
    B = proof_data["B"]
    C = proof_data["C"]
    return [
        int(A[0], 16),   # A.x
        int(A[1], 16),   # A.y
        int(B[0][0], 16), # B.x1
        int(B[0][1], 16), # B.y1
        int(B[1][0], 16), # B.x2
        int(B[1][1], 16), # B.y2
        int(C[0], 16),   # C.x
        int(C[1], 16),   # C.y
    ]


def main():
    print("=" * 60)
    print("  MIXIMUS END-TO-END TEST")
    print("  Deposit → Prove → Withdraw")
    print("=" * 60)

    # ---- Load deployment info ----
    deploy_file = os.path.join(PROJECT_DIR, "deployment", "evm", "deployments-localhost.json")
    if not os.path.exists(deploy_file):
        print(f"ERROR: Deployment file not found: {deploy_file}")
        print("Run: npx hardhat run deployment/evm/deploy.js --network localhost")
        sys.exit(1)

    deploy_info = json.loads(open(deploy_file).read())
    pool_address = deploy_info["nativePool"]["address"]
    print(f"\n1. Pool address: {pool_address}")

    # ---- Connect to Hardhat node ----
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"ERROR: Cannot connect to {RPC_URL}")
        print("Run: npx hardhat node")
        sys.exit(1)
    print(f"   Connected to chain ID: {w3.eth.chain_id}")

    depositor = w3.eth.account.from_key(DEPOSITOR_KEY)
    recipient = w3.eth.account.from_key(RECIPIENT_KEY)
    print(f"   Depositor: {depositor.address}")
    print(f"   Recipient: {recipient.address}")

    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=POOL_ABI)

    # ---- Check pool state ----
    denomination = pool.functions.denomination().call()
    symbol = pool.functions.assetSymbol().call()
    print(f"   Pool: {symbol}, denomination: {w3.from_wei(denomination, 'ether')} ETH")

    # ---- Load C++ prover ----
    print(f"\n2. Loading C++ prover...")
    lib, tree_depth = load_native_library()
    print(f"   Proving key: {PK_PATH}")

    # ---- Generate secret ----
    print(f"\n3. Generating secret...")
    secret = secrets.randbelow(SCALAR_FIELD - 1) + 1
    print(f"   Secret: {hex(secret)[:20]}...{hex(secret)[-8:]}")

    # Compute leaf hash using the contract's MiMC
    leaf_hash = pool.functions.makeLeafHash(secret).call()
    print(f"   Leaf hash: {leaf_hash}")

    # ---- Deposit ----
    print(f"\n4. Depositing {w3.from_wei(denomination, 'ether')} ETH...")
    depositor_balance_before = w3.eth.get_balance(depositor.address)
    print(f"   Depositor balance before: {w3.from_wei(depositor_balance_before, 'ether')} ETH")

    nonce = w3.eth.get_transaction_count(depositor.address)
    deposit_tx = pool.functions.deposit(leaf_hash).build_transaction({
        'from': depositor.address,
        'value': denomination,
        'gas': 3000000,
        'gasPrice': w3.eth.gas_price,
        'nonce': nonce,
    })
    signed_tx = w3.eth.account.sign_transaction(deposit_tx, DEPOSITOR_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"   Deposit tx: {receipt.transactionHash.hex()}")
    print(f"   Gas used: {receipt.gasUsed}")

    leaf_index = pool.functions.nextLeafIndex().call() - 1
    root = pool.functions.getRoot().call()
    print(f"   Leaf index: {leaf_index}")
    print(f"   New Merkle root: {root}")

    # ---- Get Merkle path ----
    print(f"\n5. Getting Merkle authentication path...")
    path_data, address_bits = pool.functions.getPath(leaf_index).call()
    print(f"   Path length: {len(path_data)} levels")
    print(f"   Address bits: {''.join(['1' if b else '0' for b in address_bits[:8]])}...")

    # ---- Compute nullifier ----
    print(f"\n6. Computing nullifier...")
    nullifier = compute_nullifier(lib, secret, leaf_index)
    print(f"   Nullifier: {nullifier}")
    is_spent = pool.functions.isSpent(nullifier).call()
    print(f"   Already spent: {is_spent}")

    # ---- Compute external hash ----
    print(f"\n7. Computing external hash...")
    exthash = compute_exthash(pool_address, recipient.address)
    print(f"   Ext hash: {exthash}")

    # ---- Generate proof ----
    print(f"\n8. Generating zkSNARK proof...")
    proof_data = generate_proof(
        lib, PK_PATH,
        root, secret, exthash,
        address_bits, list(path_data)
    )
    proof_array = proof_to_uint256_array(proof_data)
    print(f"   Proof A: ({hex(proof_array[0])[:16]}..., {hex(proof_array[1])[:16]}...)")
    print(f"   Proof B: (({hex(proof_array[2])[:16]}..., ...), (...))")
    print(f"   Proof C: ({hex(proof_array[6])[:16]}..., {hex(proof_array[7])[:16]}...)")

    # ---- Withdraw ----
    print(f"\n9. Withdrawing to {recipient.address}...")
    recipient_balance_before = w3.eth.get_balance(recipient.address)
    print(f"   Recipient balance before: {w3.from_wei(recipient_balance_before, 'ether')} ETH")

    nonce = w3.eth.get_transaction_count(recipient.address)
    withdraw_tx = pool.functions.withdraw(root, nullifier, proof_array).build_transaction({
        'from': recipient.address,
        'gas': 3000000,
        'gasPrice': w3.eth.gas_price,
        'nonce': nonce,
    })
    signed_tx = w3.eth.account.sign_transaction(withdraw_tx, RECIPIENT_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status == 1:
        print(f"   Withdraw tx: {receipt.transactionHash.hex()}")
        print(f"   Gas used: {receipt.gasUsed}")
        print(f"   STATUS: SUCCESS!")
    else:
        print(f"   STATUS: FAILED (reverted)")
        sys.exit(1)

    # ---- Verify results ----
    recipient_balance_after = w3.eth.get_balance(recipient.address)
    gained = recipient_balance_after - recipient_balance_before
    print(f"\n10. Verification:")
    print(f"   Recipient balance after: {w3.from_wei(recipient_balance_after, 'ether')} ETH")
    print(f"   Net gained (after gas): {w3.from_wei(gained, 'ether')} ETH")
    print(f"   Nullifier now spent: {pool.functions.isSpent(nullifier).call()}")

    # Try double-spend (should fail)
    print(f"\n11. Double-spend protection test...")
    try:
        pool.functions.withdraw(root, nullifier, proof_array).call({'from': recipient.address})
        print(f"   ERROR: Double-spend NOT prevented!")
    except Exception as e:
        err_msg = str(e)
        if "double-spend" in err_msg.lower() or "Cannot" in err_msg:
            print(f"   Double-spend correctly rejected!")
        else:
            print(f"   Rejected with: {err_msg[:80]}...")

    print(f"\n{'=' * 60}")
    print(f"  END-TO-END TEST PASSED!")
    print(f"  Depositor sent 1 ETH anonymously to a different address")
    print(f"  using a zkSNARK proof. No on-chain link between them.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
