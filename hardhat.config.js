/**
 * Hardhat Configuration for Multi-Chain EVM Deployment
 *
 * Supports deploying MiximusFactory, MiximusNative, and MiximusERC20
 * to ALL EVM-compatible chains from a single configuration.
 *
 * Usage:
 *   npx hardhat compile
 *   npx hardhat run deployment/evm/deploy.js --network ethereum
 *   npx hardhat run deployment/evm/deploy.js --network sepolia
 *   etc.
 */

require("dotenv").config();
require("@nomicfoundation/hardhat-toolbox");

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
    sources: "./contracts/evm",
    tests: "./test",
    cache: "./cache",
    artifacts: "./artifacts",
  },

  networks: {
    // ========= MAINNETS =========

    ethereum: {
      url: process.env.ETHEREUM_RPC,
      chainId: 1,
      accounts: [DEPLOYER_KEY],
    },

    bsc: {
      url: process.env.BSC_RPC,
      chainId: 56,
      accounts: [DEPLOYER_KEY],
    },

    polygon: {
      url: process.env.POLYGON_RPC,
      chainId: 137,
      accounts: [DEPLOYER_KEY],
    },

    avalanche: {
      url: process.env.AVALANCHE_RPC,
      chainId: 43114,
      accounts: [DEPLOYER_KEY],
    },

    arbitrum: {
      url: process.env.ARBITRUM_RPC,
      chainId: 42161,
      accounts: [DEPLOYER_KEY],
    },

    base: {
      url: process.env.BASE_RPC,
      chainId: 8453,
      accounts: [DEPLOYER_KEY],
    },

    optimism: {
      url: process.env.OPTIMISM_RPC,
      chainId: 10,
      accounts: [DEPLOYER_KEY],
    },

    cronos: {
      url: process.env.CRONOS_RPC,
      chainId: 25,
      accounts: [DEPLOYER_KEY],
    },

    moonbeam: {
      url: process.env.MOONBEAM_RPC,
      chainId: 1284,
      accounts: [DEPLOYER_KEY],
    },

    ethereum_classic: {
      url: process.env.ETC_RPC,
      chainId: 61,
      accounts: [DEPLOYER_KEY],
    },

    qtum: {
      url: process.env.QTUM_RPC,
      chainId: 81,
      accounts: [DEPLOYER_KEY],
    },

    vechain: {
      url: process.env.VECHAIN_RPC,
      chainId: 100009,
      accounts: [DEPLOYER_KEY],
    },

    // ========= TESTNETS =========

    sepolia: {
      url: process.env.SEPOLIA_RPC,
      chainId: 11155111,
      accounts: [DEPLOYER_KEY],
    },

    holesky: {
      url: process.env.HOLESKY_RPC,
      chainId: 17000,
      accounts: [DEPLOYER_KEY],
    },

    bsc_testnet: {
      url: process.env.BSC_TESTNET_RPC,
      chainId: 97,
      accounts: [DEPLOYER_KEY],
    },

    polygon_amoy: {
      url: process.env.POLYGON_AMOY_RPC,
      chainId: 80002,
      accounts: [DEPLOYER_KEY],
    },

    avalanche_fuji: {
      url: process.env.AVALANCHE_FUJI_RPC,
      chainId: 43113,
      accounts: [DEPLOYER_KEY],
    },

    arbitrum_sepolia: {
      url: process.env.ARBITRUM_SEPOLIA_RPC,
      chainId: 421614,
      accounts: [DEPLOYER_KEY],
    },

    base_sepolia: {
      url: process.env.BASE_SEPOLIA_RPC,
      chainId: 84532,
      accounts: [DEPLOYER_KEY],
    },

    optimism_sepolia: {
      url: process.env.OPTIMISM_SEPOLIA_RPC,
      chainId: 11155420,
      accounts: [DEPLOYER_KEY],
    },
  },

  etherscan: {
    apiKey: process.env.ETHERSCAN_KEY || "",
  },
};
