/**
 * Deploy only the USDC mixer on Tron Nile (previous attempt ran out of energy).
 * TestTRC20 USDC already at: TTh2LQ1m6o1cToLR3jmfBqYzp8MYMtMXXH
 */

const { TronWeb } = require("tronweb");
const solc = require("solc");
const fs = require("fs");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, "../../.env") });

const PRIVATE_KEY = (process.env.DEPLOYER_PRIVATE_KEY || "").replace(/^0x/, "");
const TEST_USDC_ADDRESS = "TTh2LQ1m6o1cToLR3jmfBqYzp8MYMtMXXH";
const DENOMINATION = 1000000;

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

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function main() {
  const tronWeb = new TronWeb({ fullHost: "https://nile.trongrid.io", privateKey: PRIVATE_KEY });
  const deployer = tronWeb.defaultAddress.base58;
  const balance = await tronWeb.trx.getBalance(deployer);
  console.log(`Deployer: ${deployer}`);
  console.log(`Balance:  ${(balance / 1e6).toFixed(2)} TRX\n`);

  const { vk, vkGammaABC } = loadVerifyingKey();

  // Compile
  const source = fs.readFileSync(path.join(__dirname, "../../contracts/tron/MiximusTRC20.sol"), "utf8");
  const input = {
    language: "Solidity",
    sources: { "MiximusTRC20.sol": { content: source } },
    settings: { optimizer: { enabled: true, runs: 200 }, viaIR: true, outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } } },
  };
  console.log("Compiling MiximusTRC20...");
  const output = JSON.parse(solc.compile(JSON.stringify(input)));
  const compiled = output.contracts["MiximusTRC20.sol"]["MiximusTRC20"];

  // Deploy
  console.log("Deploying MiximusTRC20 (USDC)...");
  const tx = await tronWeb.transactionBuilder.createSmartContract({
    abi: compiled.abi,
    bytecode: compiled.evm.bytecode.object,
    feeLimit: 3000000000,
    callValue: 0,
    parameters: [TEST_USDC_ADDRESS, DENOMINATION, "USDC", vk, vkGammaABC],
  }, tronWeb.defaultAddress.hex);

  const signed = await tronWeb.trx.sign(tx);
  const receipt = await tronWeb.trx.sendRawTransaction(signed);
  if (!receipt.result) throw new Error(`Deploy failed: ${JSON.stringify(receipt)}`);

  const txID = receipt.txid;
  console.log(`TX: ${txID}`);
  console.log("Waiting for confirmation...");
  await sleep(8000);

  let contractAddress;
  for (let i = 0; i < 20; i++) {
    const info = await tronWeb.trx.getTransactionInfo(txID);
    if (info && info.contract_address) {
      contractAddress = tronWeb.address.fromHex(info.contract_address);
      break;
    }
    if (info && info.receipt && info.receipt.result === "OUT_OF_ENERGY") {
      throw new Error("OUT_OF_ENERGY");
    }
    if (info && info.receipt && info.receipt.result === "REVERT") {
      throw new Error("REVERT: " + (info.contractResult?.[0] || "unknown"));
    }
    console.log(`  Attempt ${i + 1}/20 - waiting 5s...`);
    await sleep(5000);
  }

  if (!contractAddress) throw new Error("Could not get contract address");

  console.log(`\nUSDC Mixer: ${contractAddress}`);
  console.log(`Explorer: https://nile.tronscan.org/#/contract/${contractAddress}`);

  const remaining = await tronWeb.trx.getBalance(deployer);
  console.log(`Remaining: ${(remaining / 1e6).toFixed(2)} TRX`);
  console.log(`Used: ${((balance - remaining) / 1e6).toFixed(2)} TRX`);
}

main().catch(e => { console.error(e); process.exitCode = 1; });
