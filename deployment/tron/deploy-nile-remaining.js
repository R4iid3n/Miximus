/**
 * Deploy remaining Tron Nile contracts (USDT mixer + USDC mixer).
 * TestTRC20 (USDC) already deployed at TTh2LQ1m6o1cToLR3jmfBqYzp8MYMtMXXH
 * Nile USDT already exists at TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj
 *
 * Usage:
 *   cd deployment/tron
 *   node deploy-nile-remaining.js
 */

const { TronWeb } = require("tronweb");
const solc = require("solc");
const fs = require("fs");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, "../../.env") });

const PRIVATE_KEY = (process.env.DEPLOYER_PRIVATE_KEY || "").replace(/^0x/, "");

// Already deployed
const NILE_USDT_ADDRESS = "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj";
const TEST_USDC_ADDRESS = "TTh2LQ1m6o1cToLR3jmfBqYzp8MYMtMXXH";
const DENOMINATION = 1000000; // 1 token (6 decimals)

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
  }

  const compiled = output.contracts[`${contractName}.sol`][contractName];
  return { abi: compiled.abi, bytecode: compiled.evm.bytecode.object };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function deployContract(tronWeb, { abi, bytecode }, constructorParams, name, feeLimit) {
  console.log(`  Deploying ${name}...`);

  const tx = await tronWeb.transactionBuilder.createSmartContract(
    {
      abi,
      bytecode,
      feeLimit: feeLimit || 3000000000,
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
  console.log("  Waiting for confirmation...");
  await sleep(8000);

  let contractAddress;
  for (let attempt = 0; attempt < 20; attempt++) {
    try {
      const info = await tronWeb.trx.getTransactionInfo(txID);
      if (info && info.contract_address) {
        contractAddress = tronWeb.address.fromHex(info.contract_address);
        break;
      }
      if (info && info.receipt && info.receipt.result === "OUT_OF_ENERGY") {
        throw new Error(`OUT_OF_ENERGY — need more TRX or lower fee limit`);
      }
      if (info && info.receipt && info.receipt.result === "REVERT") {
        const reason = info.contractResult?.[0] || "unknown";
        throw new Error(`REVERT: ${reason}`);
      }
    } catch (e) {
      if (e.message.includes("OUT_OF_ENERGY") || e.message.includes("REVERT")) throw e;
    }
    console.log(`  Attempt ${attempt + 1}/20 - waiting 5s...`);
    await sleep(5000);
  }

  if (!contractAddress) {
    throw new Error(`Could not get contract address for TX ${txID}`);
  }

  console.log(`  ${name}: ${contractAddress}`);
  console.log(`  Explorer: https://nile.tronscan.org/#/contract/${contractAddress}\n`);
  return contractAddress;
}

async function main() {
  if (!PRIVATE_KEY || PRIVATE_KEY.length !== 64) {
    console.error("ERROR: Set DEPLOYER_PRIVATE_KEY in .env");
    process.exit(1);
  }

  const tronWeb = new TronWeb({
    fullHost: "https://nile.trongrid.io",
    privateKey: PRIVATE_KEY,
  });

  const deployer = tronWeb.defaultAddress.base58;
  const balance = await tronWeb.trx.getBalance(deployer);

  console.log("\n============================================================");
  console.log("Deploying Remaining Tron Nile Contracts");
  console.log(`Deployer: ${deployer}`);
  console.log(`Balance:  ${(balance / 1e6).toFixed(2)} TRX`);
  console.log("============================================================\n");

  const { vk, vkGammaABC } = loadVerifyingKey();
  console.log(`VK loaded: ${vk.length} values, gammaABC: ${vkGammaABC.length} values\n`);

  // Compile
  const contractsDir = path.join(__dirname, "../../contracts/tron");
  const miximusTRC20 = compileSolidity("MiximusTRC20", path.join(contractsDir, "MiximusTRC20.sol"));
  console.log("  Compilation complete!\n");

  // ─── 1. Deploy USDT Mixer ────────────────────────────────────────────────

  console.log("1/2 Deploying MiximusTRC20 (USDT)...");
  const usdtMixerAddr = await deployContract(
    tronWeb,
    miximusTRC20,
    [NILE_USDT_ADDRESS, DENOMINATION, "USDT", vk, vkGammaABC],
    "MiximusTRC20 (USDT)"
  );

  console.log("  Waiting 10s for nonce sync...");
  await sleep(10000);

  // ─── 2. Deploy USDC Mixer ────────────────────────────────────────────────

  console.log("2/2 Deploying MiximusTRC20 (USDC)...");
  const usdcMixerAddr = await deployContract(
    tronWeb,
    miximusTRC20,
    [TEST_USDC_ADDRESS, DENOMINATION, "USDC", vk, vkGammaABC],
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
        tokenAddress: TEST_USDC_ADDRESS,
        denomination: "1 USDC",
        denominationRaw: DENOMINATION.toString(),
        symbol: "USDC",
        type: "trc20",
      },
    },
    tokens: {
      USDT: NILE_USDT_ADDRESS,
      USDC: TEST_USDC_ADDRESS,
    },
  };

  const outputPath = path.join(__dirname, "deployments-nile.json");
  fs.writeFileSync(outputPath, JSON.stringify(deploymentInfo, null, 2));
  console.log(`Saved to: ${outputPath}`);

  const remaining = await tronWeb.trx.getBalance(deployer);

  console.log("\n============================================================");
  console.log("ALL TRON NILE DEPLOYMENTS COMPLETE");
  console.log("============================================================");
  console.log(`  USDT Token:  ${NILE_USDT_ADDRESS}`);
  console.log(`  USDC Token:  ${TEST_USDC_ADDRESS}`);
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
