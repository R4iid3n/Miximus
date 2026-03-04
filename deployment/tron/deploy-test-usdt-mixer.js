/**
 * Deploy TestTRC20 (mock USDT) + new MiximusTRC20 on Tron Nile.
 *
 * The existing mixer uses TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf as its token,
 * but that token's transfer() silently fails when called from a smart contract
 * (likely a 20-byte vs 21-byte Tron address encoding mismatch inside TXYZop).
 *
 * This script:
 *   1. Deploys TestTRC20 — a minimal, fully-standard TRC20 we control
 *   2. Mints 100,000 USDT to the deployer wallet (for pool seeding)
 *   3. Deploys a new MiximusTRC20 bound to our TestTRC20
 *   4. Updates deployments-nile.json and config/assets_testnet.json
 *
 * Usage:
 *   cd deployment/tron
 *   node deploy-test-usdt-mixer.js
 */

const { TronWeb } = require("tronweb");
const solc = require("solc");
const fs = require("fs");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, "../../.env") });

const PRIVATE_KEY = (process.env.DEPLOYER_PRIVATE_KEY || "").replace(/^0x/, "");

const DENOMINATION = 1_000_000; // 1 USDT (6 decimals)
const MINT_AMOUNT  = 100_000_000_000; // 100,000 USDT to deployer for seeding

// ─── Helpers ────────────────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ─── Compilation ────────────────────────────────────────────────────────────────

function compileContract(fileName, contractName) {
  const filePath = path.join(__dirname, "../../contracts/tron", fileName);
  const source = fs.readFileSync(filePath, "utf8");

  const input = {
    language: "Solidity",
    sources: { [fileName]: { content: source } },
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
    output.errors
      .filter((e) => e.severity === "warning")
      .forEach((e) => console.warn(`  [warn] ${e.message}`));
  }

  const compiled = output.contracts[fileName][contractName];
  return { abi: compiled.abi, bytecode: compiled.evm.bytecode.object };
}

// ─── Verifying key ──────────────────────────────────────────────────────────────

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

// ─── Deploy helper ──────────────────────────────────────────────────────────────

async function deployContract(tronWeb, { abi, bytecode }, constructorParams, name) {
  console.log(`  Deploying ${name}...`);

  const tx = await tronWeb.transactionBuilder.createSmartContract(
    {
      abi,
      bytecode,
      feeLimit: 3_000_000_000, // 3000 TRX
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
        if (receiptResult && receiptResult !== "SUCCESS") {
          throw new Error(
            `Deploy TX failed — receipt.result="${receiptResult}". ` +
            `Ensure deployer has ≥500 TRX and re-run.`
          );
        }
        contractAddress = tronWeb.address.fromHex(info.contract_address);
        break;
      }
    } catch (e) {
      if (e.message.includes("receipt.result")) throw e;
    }
    console.log(`  Attempt ${attempt + 1}/20 — waiting 5s...`);
    await sleep(5000);
  }

  if (!contractAddress) {
    throw new Error(`Could not get contract address for TX ${txID}`);
  }

  console.log(`  ${name}: ${contractAddress}`);
  console.log(`  Explorer: https://nile.tronscan.org/#/contract/${contractAddress}\n`);
  return contractAddress;
}

// ─── triggerSmartContract helper ────────────────────────────────────────────────

async function callContract(tronWeb, contractAddress, abi, funcName, params, feeLimit = 100_000_000) {
  const func = abi.find((f) => f.name === funcName && f.type === "function");
  if (!func) throw new Error(`Function ${funcName} not found in ABI`);

  const tx = await tronWeb.transactionBuilder.triggerSmartContract(
    contractAddress,
    `${funcName}(${func.inputs.map((i) => i.type).join(",")})`,
    { feeLimit, callValue: 0 },
    params.map((p, i) => ({ type: func.inputs[i].type, value: p })),
    tronWeb.defaultAddress.hex
  );

  if (!tx.result?.result) {
    throw new Error(`triggerSmartContract failed: ${JSON.stringify(tx)}`);
  }

  const signed = await tronWeb.trx.sign(tx.transaction);
  const receipt = await tronWeb.trx.sendRawTransaction(signed);
  if (!receipt.result) throw new Error(`TX broadcast failed: ${JSON.stringify(receipt)}`);

  const txID = receipt.txid;
  console.log(`    ${funcName} TX: ${txID}`);
  await sleep(6000);

  const info = await tronWeb.trx.getTransactionInfo(txID);
  if (info?.receipt?.result && info.receipt.result !== "SUCCESS") {
    throw new Error(`${funcName} TX failed: ${info.receipt.result}`);
  }
  return txID;
}

// ─── Main ────────────────────────────────────────────────────────────────────────

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
  console.log("Deploy TestTRC20 + new MiximusTRC20 (Tron Nile)");
  console.log(`Deployer: ${deployer}`);
  console.log(`Balance:  ${(balance / 1e6).toFixed(2)} TRX`);
  console.log("============================================================\n");

  if (balance < 800_000_000) {
    console.error("ERROR: Need at least 800 TRX for two contract deployments.");
    console.error("Get test TRX from: https://nileex.io/join/getJoinPage");
    process.exit(1);
  }

  // 1. Compile
  const testTRC20 = compileContract("TestTRC20.sol", "TestTRC20");
  const miximusTRC20 = compileContract("MiximusTRC20.sol", "MiximusTRC20");
  const { vk, vkGammaABC } = loadVerifyingKey();
  console.log(`  VK loaded — gammaABC entries: ${vkGammaABC.length / 2}\n`);

  // 2. Deploy TestTRC20
  console.log("Step 1: Deploying TestTRC20 (mock USDT)...");
  const tokenAddr = await deployContract(
    tronWeb,
    testTRC20,
    ["Tether USD", "USDT", 6],
    "TestTRC20 (USDT)"
  );

  // 3. Mint tokens to deployer
  console.log(`Step 2: Minting ${MINT_AMOUNT / 1e6} USDT to deployer...`);
  await callContract(tronWeb, tokenAddr, testTRC20.abi, "mint", [deployer, MINT_AMOUNT]);
  console.log("  Mint complete.\n");

  // 4. Deploy new MiximusTRC20
  console.log("Step 3: Deploying MiximusTRC20 (bound to TestTRC20)...");
  const mixerAddr = await deployContract(
    tronWeb,
    miximusTRC20,
    [tokenAddr, DENOMINATION, "USDT", vk, vkGammaABC],
    "MiximusTRC20 (USDT)"
  );

  // 5. Update deployments-nile.json
  const deploymentsPath = path.join(__dirname, "deployments-nile.json");
  let existing = {};
  if (fs.existsSync(deploymentsPath)) {
    existing = JSON.parse(fs.readFileSync(deploymentsPath, "utf8"));
  }
  existing.contracts = existing.contracts || {};
  existing.tokens = existing.tokens || {};
  existing.contracts.USDT = {
    contract:         "MiximusTRC20",
    address:          mixerAddr,
    tokenAddress:     tokenAddr,
    denomination:     "1 USDT",
    denominationRaw:  DENOMINATION.toString(),
    symbol:           "USDT",
    type:             "trc20",
    note:             "Uses TestTRC20 — avoids TXYZop transfer() bug",
  };
  existing.tokens.USDT = tokenAddr;
  existing.lastUsdtRedeploy = new Date().toISOString();
  fs.writeFileSync(deploymentsPath, JSON.stringify(existing, null, 2));
  console.log("  deployments-nile.json updated.\n");

  // 6. Update config/assets_testnet.json
  const assetsCfgPath = path.join(__dirname, "../../config/assets_testnet.json");
  const assetsCfg = JSON.parse(fs.readFileSync(assetsCfgPath, "utf8"));
  const usdtEntry = assetsCfg.assets.stablecoins.find(
    (a) => a.symbol === "USDT" && a.chain === "tron"
  );
  if (usdtEntry) {
    usdtEntry.contract = tokenAddr;
    usdtEntry.mixer_contract = mixerAddr;
    usdtEntry._note = "TestTRC20 mock USDT on Tron Nile — avoids TXYZop transfer() restriction";
    fs.writeFileSync(assetsCfgPath, JSON.stringify(assetsCfg, null, 2));
    console.log("  config/assets_testnet.json updated.\n");
  } else {
    console.warn("  WARNING: USDT/tron entry not found in assets_testnet.json — update manually.");
  }

  // 7. Patch webapp/backend/seed_pools.py (hardcoded USDT mixer_contract)
  const seedPoolsPath = path.join(__dirname, "../../webapp/backend/seed_pools.py");
  if (fs.existsSync(seedPoolsPath)) {
    let seedPoolsContent = fs.readFileSync(seedPoolsPath, "utf8");
    // Replace old mixer_contract for USDT/tron
    const oldMixer = existing.contracts?.USDT?.address || "TTPPEKMUWjATr4kQrfUz9ombiC7oHofnVB";
    seedPoolsContent = seedPoolsContent.replace(
      new RegExp(`("mixer_contract":\\s*")${oldMixer}(")`, "g"),
      `$1${mixerAddr}$2`
    );
    fs.writeFileSync(seedPoolsPath, seedPoolsContent);
    console.log("  webapp/backend/seed_pools.py updated.\n");
  } else {
    console.warn("  WARNING: seed_pools.py not found — update mixer_contract manually.");
  }

  const remaining = await tronWeb.trx.getBalance(deployer);
  console.log("============================================================");
  console.log("DEPLOYMENT COMPLETE");
  console.log("============================================================");
  console.log(`  TestTRC20 (token):    ${tokenAddr}`);
  console.log(`  MiximusTRC20 (mixer): ${mixerAddr}`);
  console.log(`  TRX used:             ${((balance - remaining) / 1e6).toFixed(2)}`);
  console.log("\n=== NEXT STEPS ===");
  console.log("1. Restart the Flask backend (to reload the new contract addresses)");
  console.log("2. Seed the USDT pool:");
  console.log("   cd /mnt/c/AML\\ mixer/webapp/backend");
  console.log("   python seed_units.py --symbol USDT --chain tron --network-mode testnet --units 5");
  console.log("3. Place a new USDT/Tron order and verify the full cycle completes.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
