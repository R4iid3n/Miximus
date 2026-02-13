/**
 * Deploy Miximus contracts on Tron Nile testnet.
 *
 * Deploys:
 *   1. TestTRC20 (USDC)  — test token for USDC mixer
 *   2. MiximusTRC20 (USDT) — mixer for Nile USDT
 *   3. MiximusTRC20 (USDC) — mixer for test USDC
 *
 * Nile testnet USDT already exists at: TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj
 *
 * Usage:
 *   cd deployment/tron
 *   npm install
 *   node deploy-nile.js
 */

const { TronWeb } = require("tronweb");
const solc = require("solc");
const fs = require("fs");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, "../../.env") });

// ─── Config ────────────────────────────────────────────────────────────────────

const NILE_FULL_NODE = "https://nile.trongrid.io";
const NILE_SOLIDITY_NODE = "https://nile.trongrid.io";
const NILE_EVENT_SERVER = "https://event.nileex.io";

// Strip 0x prefix — TronWeb expects raw hex private key
const PRIVATE_KEY = (process.env.DEPLOYER_PRIVATE_KEY || "").replace(/^0x/, "");

// Existing Nile testnet USDT (official Tron testnet token)
const NILE_USDT_ADDRESS = "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj";

const DENOMINATION = 1000000; // 1 token (6 decimals)

// ─── Verifying key ─────────────────────────────────────────────────────────────

function loadVerifyingKey() {
  const vkPath = path.join(__dirname, "../../ethsnarks-miximus/.keys/miximus.vk.json");
  const vkData = JSON.parse(fs.readFileSync(vkPath, "utf8"));

  const vk = [
    vkData.alpha[0], vkData.alpha[1],
    vkData.beta[0][0], vkData.beta[0][1],
    vkData.beta[1][0], vkData.beta[1][1],
    vkData.gamma[0][0], vkData.gamma[0][1],
    vkData.gamma[1][0], vkData.gamma[1][1],
    vkData.delta[0][0], vkData.delta[0][1],
    vkData.delta[1][0], vkData.delta[1][1],
  ];

  const vkGammaABC = [];
  for (const point of vkData.gammaABC) {
    vkGammaABC.push(point[0]);
    vkGammaABC.push(point[1]);
  }

  return { vk, vkGammaABC };
}

// ─── Solidity compilation ──────────────────────────────────────────────────────

function compileSolidity(contractName, filePath) {
  const source = fs.readFileSync(filePath, "utf8");

  const input = {
    language: "Solidity",
    sources: { [`${contractName}.sol`]: { content: source } },
    settings: {
      optimizer: { enabled: true, runs: 200 },
      viaIR: true,
      outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } },
    },
  };

  console.log(`  Compiling ${contractName}...`);
  const output = JSON.parse(solc.compile(JSON.stringify(input)));

  if (output.errors) {
    const fatal = output.errors.filter((e) => e.severity === "error");
    if (fatal.length > 0) {
      console.error("Compilation errors:");
      fatal.forEach((e) => console.error(e.formattedMessage));
      process.exit(1);
    }
    // Print warnings
    output.errors
      .filter((e) => e.severity === "warning")
      .forEach((e) => console.warn(`  [warn] ${e.message}`));
  }

  const compiled = output.contracts[`${contractName}.sol`][contractName];
  return {
    abi: compiled.abi,
    bytecode: compiled.evm.bytecode.object,
  };
}

// ─── Deploy helper ─────────────────────────────────────────────────────────────

async function deployContract(tronWeb, { abi, bytecode }, constructorParams, name, feeLimit) {
  console.log(`  Deploying ${name}...`);

  const tx = await tronWeb.transactionBuilder.createSmartContract(
    {
      abi,
      bytecode,
      feeLimit: feeLimit || 3000000000, // 3000 TRX default fee limit
      callValue: 0,
      parameters: constructorParams,
    },
    tronWeb.defaultAddress.hex
  );

  const signed = await tronWeb.trx.sign(tx);
  const receipt = await tronWeb.trx.sendRawTransaction(signed);

  if (!receipt.result) {
    throw new Error(`Deploy failed: ${JSON.stringify(receipt)}`);
  }

  const txID = receipt.txid || receipt.transaction?.txID;
  console.log(`  TX: ${txID}`);

  // Wait for confirmation
  console.log("  Waiting for confirmation...");
  await sleep(6000);

  // Get contract address from transaction info
  let contractAddress;
  for (let attempt = 0; attempt < 15; attempt++) {
    try {
      const info = await tronWeb.trx.getTransactionInfo(txID);
      if (info && info.contract_address) {
        // v6 returns hex string like "41..."
        const hexAddr = typeof info.contract_address === "string"
          ? info.contract_address
          : info.contract_address;
        contractAddress = tronWeb.address.fromHex(hexAddr);
        break;
      }
      if (info && info.receipt && info.receipt.result === "OUT_OF_ENERGY") {
        throw new Error("OUT_OF_ENERGY — increase fee limit");
      }
    } catch (e) {
      if (e.message && e.message.includes("OUT_OF_ENERGY")) throw e;
      // Not confirmed yet
    }
    console.log(`  Attempt ${attempt + 1}/15 - waiting 5s...`);
    await sleep(5000);
  }

  if (!contractAddress) {
    throw new Error(`Could not get contract address for TX ${txID}`);
  }

  console.log(`  ${name}: ${contractAddress}`);
  console.log(`  Explorer: https://nile.tronscan.org/#/contract/${contractAddress}`);
  return contractAddress;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ─── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  if (!PRIVATE_KEY || PRIVATE_KEY.length !== 64) {
    console.error("ERROR: Set DEPLOYER_PRIVATE_KEY in .env (hex, 64 chars)");
    process.exit(1);
  }

  const tronWeb = new TronWeb({
    fullHost: NILE_FULL_NODE,
    privateKey: PRIVATE_KEY,
  });

  const deployer = tronWeb.defaultAddress.base58;
  const balance = await tronWeb.trx.getBalance(deployer);

  console.log("\n============================================================");
  console.log("Deploying Miximus Contracts on Tron Nile Testnet");
  console.log(`Deployer: ${deployer}`);
  console.log(`Balance:  ${(balance / 1e6).toFixed(2)} TRX`);
  console.log("============================================================\n");

  if (balance < 200000000) {
    console.error("ERROR: Need at least 200 TRX for deployment. Get from https://nileex.io/join/getJoinPage");
    process.exit(1);
  }

  // Load verifying key
  const { vk, vkGammaABC } = loadVerifyingKey();
  console.log(`VK loaded: ${vk.length} values, gammaABC: ${vkGammaABC.length} values\n`);

  // ─── Compile contracts ──────────────────────────────────────────────────────

  const contractsDir = path.join(__dirname, "../../contracts/tron");

  const testTRC20 = compileSolidity("TestTRC20", path.join(contractsDir, "TestTRC20.sol"));
  const miximusTRC20 = compileSolidity("MiximusTRC20", path.join(contractsDir, "MiximusTRC20.sol"));

  console.log("  Compilation complete!\n");

  // ─── 1. Deploy Test USDC Token ────────────────────────────────────────────

  console.log("1/3 Deploying TestTRC20 (USDC)...");
  const usdcTokenAddr = await deployContract(
    tronWeb,
    testTRC20,
    ["Test USD Coin", "USDC", 6],
    "TestTRC20 (USDC)",
    1000000000
  );

  // Mint 1000 test USDC
  console.log("  Minting 1000 test USDC...");
  const usdcContract = await tronWeb.contract(testTRC20.abi, usdcTokenAddr);
  await usdcContract.mint(deployer, 1000 * 1e6).send({ feeLimit: 100000000 });
  console.log("  Minted 1000 USDC\n");

  await sleep(5000);

  // ─── 2. Deploy USDT Mixer ────────────────────────────────────────────────

  console.log("2/3 Deploying MiximusTRC20 (USDT)...");
  const usdtMixerAddr = await deployContract(
    tronWeb,
    miximusTRC20,
    [NILE_USDT_ADDRESS, DENOMINATION, "USDT", vk, vkGammaABC],
    "MiximusTRC20 (USDT)"
  );

  await sleep(5000);

  // ─── 3. Deploy USDC Mixer ────────────────────────────────────────────────

  console.log("3/3 Deploying MiximusTRC20 (USDC)...");
  const usdcMixerAddr = await deployContract(
    tronWeb,
    miximusTRC20,
    [usdcTokenAddr, DENOMINATION, "USDC", vk, vkGammaABC],
    "MiximusTRC20 (USDC)"
  );

  // ─── Save deployment info ────────────────────────────────────────────────

  const deploymentInfo = {
    network: "nile",
    deployer,
    deployedAt: new Date().toISOString(),
    contracts: {
      USDT: {
        contract: "MiximusTRC20",
        address: usdtMixerAddr,
        tokenAddress: NILE_USDT_ADDRESS,
        denomination: "1 USDT",
        denominationRaw: DENOMINATION.toString(),
        symbol: "USDT",
        type: "trc20",
      },
      USDC: {
        contract: "MiximusTRC20",
        address: usdcMixerAddr,
        tokenAddress: usdcTokenAddr,
        denomination: "1 USDC",
        denominationRaw: DENOMINATION.toString(),
        symbol: "USDC",
        type: "trc20",
      },
    },
    tokens: {
      USDT: NILE_USDT_ADDRESS,
      USDC: usdcTokenAddr,
    },
  };

  const outputPath = path.join(__dirname, "deployments-nile.json");
  fs.writeFileSync(outputPath, JSON.stringify(deploymentInfo, null, 2));
  console.log(`\nSaved to: ${outputPath}`);

  const remaining = await tronWeb.trx.getBalance(deployer);

  console.log("\n============================================================");
  console.log("ALL TRON NILE DEPLOYMENTS COMPLETE");
  console.log("============================================================");
  console.log(`  USDT Token:  ${NILE_USDT_ADDRESS}`);
  console.log(`  USDC Token:  ${usdcTokenAddr}`);
  console.log(`  USDT Mixer:  ${usdtMixerAddr}`);
  console.log(`  USDC Mixer:  ${usdcMixerAddr}`);
  console.log("============================================================");
  console.log(`Remaining: ${(remaining / 1e6).toFixed(2)} TRX`);
  console.log(`Used:      ${((balance - remaining) / 1e6).toFixed(2)} TRX\n`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
