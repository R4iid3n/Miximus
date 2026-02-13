# Miximus — Multi-Chain zkSNARK Cryptocurrency Mixer

A privacy-preserving cryptocurrency mixer powered by **Groth16 zkSNARK proofs** on the **BN254 curve**. Users deposit fixed-denomination units into a Merkle tree smart contract and withdraw to a different address — the zero-knowledge proof guarantees the withdrawal is valid without revealing which deposit it corresponds to.

Built on top of [ethsnarks-miximus](https://github.com/HarryR/ethsnarks-miximus/tree/1b9fbef81fe803fcd634a3c0f9ad6e94dbf9a6ff) by HarryR, extended from a single-chain ETH-only prototype into a **multi-chain custodial mixing service** supporting **Ethereum** (ETH, USDT, USDC), **Tron** (USDT, USDC), and **Bitcoin**.

---

## Original Work — ethsnarks-miximus

The [original project](https://github.com/HarryR/ethsnarks-miximus/tree/1b9fbef81fe803fcd634a3c0f9ad6e94dbf9a6ff) by [HarryR](https://github.com/HarryR) provided:

- **zkSNARK circuit** (C++ / libsnark): A Groth16 circuit that proves knowledge of a secret corresponding to a leaf in a depth-29 Merkle tree, using MiMC hashing. The public input is compressed into a single hash: `pub_hash = MiMC(root, nullifier, ext_hash)`.
- **Solidity contract** (v0.5): A single `Miximus.sol` contract for ETH deposits/withdrawals on Ethereum, with on-chain BN254 pairing verification.
- **Python tooling**: Secret generation, leaf hash computation, and proof generation via ctypes FFI to the C++ prover library.
- **Trusted setup**: Proving key (`miximus.pk.raw`) and verifying key (`miximus.vk.json`) for the circuit.

The original was a proof-of-concept — single chain (Ethereum only), single denomination (1 ETH), self-custody model (users manage their own secrets and submit proofs directly).

## What I Built

I took the core cryptographic primitives (circuit, prover, MiMC hash) and built a production-grade multi-chain mixing service around them:

### Smart Contracts

Rewrote contracts from Solidity 0.5 to **0.8.19** with a modular architecture:

| Contract | Purpose |
|----------|---------|
| `MiximusBase.sol` | Abstract base — Merkle tree, MiMC hasher, Groth16 verifier, deposit/withdraw logic |
| `MiximusNative.sol` | Native currency pools (ETH) |
| `MiximusERC20.sol` | ERC20 token pools (USDT, USDC) |
| `MiximusFactory.sol` | Permissionless factory for deploying new mixer pools |

Added `withdrawViaRelayer()` — enables the operator to submit withdrawal proofs on behalf of users, deducting a fee from the denomination. This is what makes the custodial model possible.

**Tron** — Solidity-compatible contracts deployed on TVM for USDT/USDC mixing.

**Bitcoin** — Custodial UTXO-based mixing via the `bit` library.

### Multi-Chain Python Backend

Extended the Python layer into a full **multi-chain orchestrator**:

```
python/
├── miximus_multichain.py    # Unified API: generate secrets, compute hashes, generate proofs
└── chain_adapters/
    ├── base.py              # Abstract adapter interface
    ├── evm.py               # Ethereum (web3.py)
    ├── tron.py              # Tron (tronpy)
    ├── btc.py               # Bitcoin (bit library, UTXO management)
    └── registry.py          # Asset/chain registry from JSON config
```

- **Same proving/verifying keys** work across all chains — the circuit is chain-agnostic
- Chain adapters handle chain-specific differences (address formats, RPC calls, transaction signing)
- Asset registry (`config/assets.json`) defines supported assets with denominations, contract addresses, and RPC endpoints

### Custodial Mixing Service (Webapp)

Full-stack web application — no wallet connection needed, users just send crypto:

**Backend** (Flask + SQLAlchemy):
- `order_processor.py` — Background worker processing orders through the pipeline: payment verification → deposit → proof generation → withdrawal
- `wallet_service.py` — Hot wallet for Ethereum, Tron, and Bitcoin — handles deposits, withdrawals, and payment verification
- `models.py` — `MixOrder` (order lifecycle), `PoolUnit` (individual Merkle tree leaves), `PoolConfig` (per-pool settings)
- `routes/mix.py` — REST API: pool listing, order creation, payment submission, status polling

**Frontend** (React + TypeScript + Vite):
- 3-step mixing wizard: Select pool → Enter amount & recipient → Send payment & track progress
- Multi-unit support: deposit multiples of the denomination in a single order
- Real-time order tracking with step-by-step progress (Payment → Deposit → Proof → Withdrawal)

### Pool Mixing Architecture

The key innovation over the original self-custody model:

1. **Operator pre-deposits** units into mixer contracts (seeds the anonymity set)
2. When a user orders, the backend **reserves existing pool units** for withdrawal
3. On payment confirmation, the backend **deposits a FRESH unit** (new secret → replenishes the pool)
4. Then **withdraws a RESERVED unit** (different, pre-existing secret → sent to user's recipient)
5. The deposited and withdrawn leaves are **different** — different secrets, different leaf indices, different timestamps → **unlinkable**

This means deposits and withdrawals can never be correlated by timing or leaf index analysis. The pool grows in anonymity as more users transact through it.

### Deployment Infrastructure

- **Hardhat** config for Ethereum (mainnet + Sepolia testnet) with one-command deployment
- **Tron** deployment scripts (tronpy) for Nile testnet and mainnet
- **Bitcoin** custodial adapter (UTXO management via `bit` library)
- Contract verification scripts (Etherscan)

### Currently Deployed (Testnet)

| Chain | Asset | Contract | Denomination |
|-------|-------|----------|-------------|
| Sepolia | ETH | `0x85A4ecCe24580f6d90adFFD74d9B061BD3A4f3c4` | 0.06 ETH |
| Sepolia | USDT | `0x7a958DBd4C3BDd7ff82ed3ffab5e895a8b49C4EA` | 1 USDT |
| Sepolia | USDC | `0xBeB1B7eA73e18fA7E588f09C9154F2781E48578b` | 1 USDC |
| Tron Nile | USDT | `TSCu8XbzpxAmdBvQjLRzzdzfckZVTRLAyr` | 1 USDT |
| Tron Nile | USDC | `TQwSctaE4f1WdpTc37c9ckyy322ab1AauU` | 1 USDC |
| BTC Testnet | BTC | Custodial | 0.002 BTC |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     React Frontend                          │
│  Pool Selection → Amount Input → Payment → Order Tracking   │
└──────────────────────────┬──────────────────────────────────┘
                           │ REST API
┌──────────────────────────▼──────────────────────────────────┐
│                     Flask Backend                            │
│  Routes (mix.py) → OrderProcessor → WalletService           │
│       │                    │              │                  │
│       ▼                    ▼              ▼                  │
│  SQLAlchemy DB      MiximusMultiChain   MultiChainWallet    │
│  (Orders, Units,    (Proof Generation)  (EVM, Tron, BTC)    │
│   Pool Configs)           │                                  │
└───────────────────────────┼──────────────────────────────────┘
                            │ ctypes FFI
┌───────────────────────────▼──────────────────────────────────┐
│              C++ Prover (libmiximus.so)                       │
│  libsnark · Groth16 · BN254 · MiMC · Depth-29 Merkle Tree   │
└──────────────────────────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│                    Smart Contracts                            │
│  Ethereum (Solidity) · Tron (TVM) · Bitcoin (Custodial)      │
│  On-chain: Deposit leaves, verify proofs, release funds      │
└──────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Node.js 18+
- Python 3.10+
- C++ compiler (for building the prover from ethsnarks-miximus)

### Setup

```bash
# Clone with submodule
git clone --recursive https://github.com/R4iid3n/Miximus.git
cd Miximus

# Configure environment
cp .env.example .env
# Edit .env — add your private key and RPC URLs

# Install JS dependencies
npm install

# Compile contracts
npx hardhat compile

# Backend
cd webapp/backend
pip install -r requirements.txt
python seed_pools.py        # Create pool configs in DB
python seed_units.py --dry-run  # Preview unit seeding (or remove --dry-run to deposit on-chain)
python app.py               # Start Flask server on :5000

# Frontend (separate terminal)
cd webapp/frontend
npm install
cp .env.example .env        # Add your WalletConnect project ID
npm run dev                 # Start Vite dev server on :5173
```

### Deploy to a New Chain

```bash
# EVM chain (e.g., Base)
npx hardhat run deployment/evm/deploy.js --network base

# Verify on explorer
npx hardhat verify --network base <CONTRACT_ADDRESS> <CONSTRUCTOR_ARGS>
```

## zkSNARK Details

| Parameter | Value |
|-----------|-------|
| Curve | BN254 (alt_bn128) |
| Proof system | Groth16 |
| Hash function | MiMC (Keccak-256 round constants) |
| Merkle tree depth | 29 (536M+ leaves) |
| Public input | `pub_hash = MiMC(root, nullifier, ext_hash)` |
| ext_hash | `sha256(abi.encodePacked(contract, recipient)) % SCALAR_FIELD` |

The proving key and verifying key are generated once during trusted setup and reused across all chains — the circuit itself is completely chain-agnostic.

## Project Structure

```
├── contracts/
│   ├── evm/                # Solidity 0.8.19 (MiximusBase, Native, ERC20, Factory)
│   └── tron/               # Tron-compatible Solidity
├── python/                 # Chain orchestrator + adapters (EVM, Tron, BTC)
├── config/                 # Asset registry (ETH, USDT, USDC, BTC)
├── deployment/             # Deploy scripts (Ethereum, Tron, BTC)
├── test/                   # Unit tests + E2E integration tests
├── ethsnarks-miximus/      # [submodule] Original C++ circuit + prover
├── webapp/
│   ├── backend/            # Flask API + order processor + wallet service
│   └── frontend/           # React + TypeScript + Vite
├── hardhat.config.js       # Hardhat configuration (Ethereum)
└── .env.example            # Environment template (no secrets)
```

## License

This project builds upon [ethsnarks-miximus](https://github.com/HarryR/ethsnarks-miximus) by HarryR, which is licensed under the LGPL-3.0 license. All original extensions and additions in this repository are provided under the same license terms.
