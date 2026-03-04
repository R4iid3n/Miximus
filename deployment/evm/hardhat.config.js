/**
 * Hardhat Configuration for Multi-Chain EVM Deployment
 *
 * Supports deploying MiximusFactory, MiximusNative, and MiximusERC20
 * to ALL EVM-compatible chains from a single configuration.
 *
 * Usage:
 *   npx hardhat deploy --network ethereum
 *   npx hardhat deploy --network bsc
 *   npx hardhat deploy --network polygon
 *   npx hardhat deploy --network avalanche
 *   etc.
 */

require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config({ path: require("path").join(__dirname, "../../.env") });

// Load environment variables (RPC URLs, private keys)
const DEPLOYER_KEY = process.env.DEPLOYER_PRIVATE_KEY || "0x" + "0".repeat(64);

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.19",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      viaIR: true,
    },
  },

  paths: {
    root: "../..",
    sources: "contracts/evm",
    tests: "deployment/evm/test",
    cache: "deployment/evm/cache",
    artifacts: "deployment/evm/artifacts",
  },

  networks: {
    // ========= MAINNETS =========

    ethereum: {
      url: process.env.ETHEREUM_RPC || "https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY",
      chainId: 1,
      accounts: [DEPLOYER_KEY],
      gasPrice: "auto",
    },

    bsc: {
      url: process.env.BSC_RPC || "https://bsc-dataseed.binance.org",
      chainId: 56,
      accounts: [DEPLOYER_KEY],
      gasPrice: 5000000000, // 5 gwei
    },

    polygon: {
      url: process.env.POLYGON_RPC || "https://polygon-rpc.com",
      chainId: 137,
      accounts: [DEPLOYER_KEY],
      gasPrice: "auto",
    },

    avalanche: {
      url: process.env.AVALANCHE_RPC || "https://api.avax.network/ext/bc/C/rpc",
      chainId: 43114,
      accounts: [DEPLOYER_KEY],
      gasPrice: 25000000000, // 25 nAVAX
    },

    arbitrum: {
      url: process.env.ARBITRUM_RPC || "https://arb1.arbitrum.io/rpc",
      chainId: 42161,
      accounts: [DEPLOYER_KEY],
    },

    base: {
      url: process.env.BASE_RPC || "https://mainnet.base.org",
      chainId: 8453,
      accounts: [DEPLOYER_KEY],
    },

    optimism: {
      url: process.env.OPTIMISM_RPC || "https://mainnet.optimism.io",
      chainId: 10,
      accounts: [DEPLOYER_KEY],
    },

    cronos: {
      url: process.env.CRONOS_RPC || "https://evm.cronos.org",
      chainId: 25,
      accounts: [DEPLOYER_KEY],
    },

    moonbeam: {
      url: process.env.MOONBEAM_RPC || "https://rpc.api.moonbeam.network",
      chainId: 1284,
      accounts: [DEPLOYER_KEY],
    },

    ethereum_classic: {
      url: process.env.ETC_RPC || "https://etc.rivet.link",
      chainId: 61,
      accounts: [DEPLOYER_KEY],
    },

    qtum: {
      url: process.env.QTUM_RPC || "https://janus.qtum.info",
      chainId: 81,
      accounts: [DEPLOYER_KEY],
    },

    vechain: {
      url: process.env.VECHAIN_RPC || "https://mainnet.veblocks.net",
      chainId: 100009,
      accounts: [DEPLOYER_KEY],
    },

    // ========= TESTNETS =========

    goerli: {
      url: process.env.GOERLI_RPC || "https://goerli.infura.io/v3/YOUR_KEY",
      chainId: 5,
      accounts: [DEPLOYER_KEY],
    },

    sepolia: {
      url: process.env.SEPOLIA_RPC || "https://sepolia.infura.io/v3/YOUR_KEY",
      chainId: 11155111,
      accounts: [DEPLOYER_KEY],
    },

    bsc_testnet: {
      url: "https://data-seed-prebsc-1-s1.binance.org:8545",
      chainId: 97,
      accounts: [DEPLOYER_KEY],
    },

    polygon_mumbai: {
      url: "https://rpc-mumbai.maticvigil.com",
      chainId: 80001,
      accounts: [DEPLOYER_KEY],
    },

    avalanche_fuji: {
      url: "https://api.avax-test.network/ext/bc/C/rpc",
      chainId: 43113,
      accounts: [DEPLOYER_KEY],
    },
  },

  // Etherscan V2 API — single key covers all chains
  etherscan: {
    apiKey: process.env.ETHERSCAN_KEY || "",
  },
};
