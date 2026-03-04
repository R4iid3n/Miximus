/**
 * Redeploy USDT Mixer on Tron Nile with TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf token.
 *
 * The previously deployed mixer used TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj.
 * This script deploys a fresh MiximusTRC20 bound to TXYZop instead.
 *
 * Usage:
 *   cd deployment/tron
 *   node deploy-nile-usdt-txyzop.js
 */

const { TronWeb } = require("tronweb");
const solc = require("solc");
const fs = require("fs");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, "../../.env") });

const PRIVATE_KEY = (process.env.DEPLOYER_PRIVATE_KEY || "").replace(/^0x/, "");

// New USDT token to bind this mixer to
const USDT_TOKEN = "TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf";
const DENOMINATION = 1000000; // 1 USDT (6 decimals)

// ─── Verifying key ─────────────────────────────────────────────────────────────

function loadVerifyingKey() {
  const vkPath = path.join(__dirname, "../../ethsnarks-miximus/.keys/miximus.vk.json");
  if (!fs.existsSync(vkPath)) {
    throw new Error(`Verifying key not found at ${vkPath}. Run keygen first.`);
  }
  const vkData = JSON.parse(fs.readFileSync(vkPath, "utf8"));

  const vk = [
    vkData.alpha[0], vkData.alpha[1],
    vkData.beta[0][0],  vkData.beta[0][1],
    vkData.beta[1][0],  vkData.beta[1][1],
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

// ─── Compilation ────────────────────────────────────────────────────────────────

function compileMiximusTRC20() {
  const filePath = path.join(__dirname, "../../contracts/tron/MiximusTRC20.sol");
  const source = fs.readFileSync(filePath, "utf8");

  const input = {
    language: "Solidity",
    sources: { "MiximusTRC20.sol": { content: source } },
    settings: {
      optimizer: { enabled: true, runs: 200 },
      viaIR: true,
      outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } },
    },
  };

  console.log("  Compiling MiximusTRC20...");
  const output = JSON.parse(solc.compile(JSON.stringify(input)));

  if (output.errors) {
    const fatal = output.errors.filter((e) => e.severity === "error");
    if (fatal.length > 0) {
      console.error("Compilation errors:");
      fatal.forEach((e) => console.error(e.formattedMessage));
      process.exit(1);
    }
    output.errors
      .filter((e) => e.severity === "warning")
      .forEach((e) => console.warn(`  [warn] ${e.message}`));
  }

  const compiled = output.contracts["MiximusTRC20.sol"]["MiximusTRC20"];
  return { abi: compiled.abi, bytecode: compiled.evm.bytecode.object };
}

// ─── Deploy helper ─────────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function deployContract(tronWeb, { abi, bytecode }, constructorParams, name) {
  console.log(`  Deploying ${name}...`);

  const tx = await tronWeb.transactionBuilder.createSmartContract(
    {
      abi,
      bytecode,
      feeLimit: 3000000000, // 3000 TRX
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
        const receiptResult = info?.receipt?.result;
        // Any result other than SUCCESS means the constructor failed
        if (receiptResult && receiptResult !== "SUCCESS") {
          throw new Error(
            `Deploy TX failed — receipt.result="${receiptResult}". ` +
            `The contract address was pre-allocated but no bytecode was stored. ` +
            `Ensure you have ≥500 TRX and re-run the script.`
          );
        }
        contractAddress = tronWeb.address.fromHex(info.contract_address);
        break;
      }
    } catch (e) {
      if (e.message.includes("receipt.result")) throw e;
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

// ─── Main ───────────────────────────────────────────────────────────────────────

async function main() {
  if (!PRIVATE_KEY || PRIVATE_KEY.length !== 64) {
    console.error("ERROR: Set DEPLOYER_PRIVATE_KEY in .env (hex, 64 chars, no 0x)");
    process.exit(1);
  }

  const tronWeb = new TronWeb({
    fullHost: "https://nile.trongrid.io",
    privateKey: PRIVATE_KEY,
  });

  const deployer = tronWeb.defaultAddress.base58;
  const balance = await tronWeb.trx.getBalance(deployer);

  console.log("\n============================================================");
  console.log("Redeploying USDT Mixer (Tron Nile) — TXYZop token");
  console.log(`Deployer: ${deployer}`);
  console.log(`Balance:  ${(balance / 1e6).toFixed(2)} TRX`);
  console.log(`Token:    ${USDT_TOKEN}`);
  console.log("============================================================\n");

  if (balance < 500000000) {
    console.error("ERROR: Need at least 500 TRX. Get from https://nileex.io/join/getJoinPage");
    process.exit(1);
  }

  const { vk, vkGammaABC } = loadVerifyingKey();
  console.log(`VK loaded — gammaABC entries: ${vkGammaABC.length / 2}\n`);

  const miximusTRC20 = compileMiximusTRC20();
  console.log("  Compilation complete!\n");

  console.log("Deploying MiximusTRC20 (USDT / TXYZop)...");
  const usdtMixerAddr = await deployContract(
    tronWeb,
    miximusTRC20,
    [USDT_TOKEN, DENOMINATION, "USDT", vk, vkGammaABC],
    "MiximusTRC20 (USDT)"
  );

  // ─── Update deployments-nile.json ──────────────────────────────────────────

  const deploymentsPath = path.join(__dirname, "deployments-nile.json");
  let existing = {};
  if (fs.existsSync(deploymentsPath)) {
    existing = JSON.parse(fs.readFileSync(deploymentsPath, "utf8"));
  }

  existing.contracts = existing.contracts || {};
  existing.tokens = existing.tokens || {};

  existing.contracts.USDT = {
    contract: "MiximusTRC20",
    address: usdtMixerAddr,
    tokenAddress: USDT_TOKEN,
    denomination: "1 USDT",
    denominationRaw: DENOMINATION.toString(),
    symbol: "USDT",
    type: "trc20",
  };
  existing.tokens.USDT = USDT_TOKEN;
  existing.lastUsdtRedeploy = new Date().toISOString();

  fs.writeFileSync(deploymentsPath, JSON.stringify(existing, null, 2));
  console.log(`deployments-nile.json updated.`);

  const remaining = await tronWeb.trx.getBalance(deployer);
  console.log("\n============================================================");
  console.log("DONE");
  console.log("============================================================");
  console.log(`  New USDT Mixer: ${usdtMixerAddr}`);
  console.log(`  Token:          ${USDT_TOKEN}`);
  console.log(`  TRX used:       ${((balance - remaining) / 1e6).toFixed(2)}`);
  console.log("\n=== NEXT STEPS ===");
  console.log("1. Update config/assets_testnet.json:");
  console.log(`   USDT contract:       "${USDT_TOKEN}"`);
  console.log(`   USDT mixer_contract: "${usdtMixerAddr}"`);
  console.log("2. Update webapp/backend/seed_pools.py:");
  console.log(`   USDT mixer_contract: "${usdtMixerAddr}"`);
  console.log("3. Re-run seed_pools.py, then seed units:");
  console.log("   python seed_pools.py");
  console.log("   python seed_units.py --symbol USDT --chain tron --network-mode testnet --units 5");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
