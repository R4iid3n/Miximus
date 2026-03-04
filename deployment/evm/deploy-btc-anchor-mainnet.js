/**
 * Deploy BTC Privacy Anchor Contract (Ethereum Mainnet)
 *
 * Deploys a MiximusNative contract with denomination = 1 wei.
 * This contract is NOT used for ETH mixing — it serves as a zkSNARK "notary"
 * for Bitcoin orders, publishing nullifiers on Ethereum to prove anonymity
 * without requiring any smart contract on the Bitcoin network itself.
 *
 * Usage (from deployment/evm/ directory):
 *   npx hardhat run deploy-btc-anchor-mainnet.js --network ethereum
 *
 * Prerequisites:
 *   - ETHEREUM_RPC and DEPLOYER_PRIVATE_KEY set in .env
 *   - At least 0.05 ETH in deployer wallet for deployment + gas
 *   - ethsnarks-miximus/.keys/miximus.vk.json must exist (run keygen first)
 */

const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

function loadVerifyingKey() {
  const vkPath = path.join(
    __dirname, "../../ethsnarks-miximus/.keys/miximus.vk.json"
  );
  if (!fs.existsSync(vkPath)) {
    throw new Error(`Verifying key not found at ${vkPath}. Run keygen first.`);
  }
  const vkData = JSON.parse(fs.readFileSync(vkPath, "utf8"));

  const vk = [
    BigInt(vkData.alpha[0]),    BigInt(vkData.alpha[1]),
    BigInt(vkData.beta[0][0]),  BigInt(vkData.beta[0][1]),
    BigInt(vkData.beta[1][0]),  BigInt(vkData.beta[1][1]),
    BigInt(vkData.gamma[0][0]), BigInt(vkData.gamma[0][1]),
    BigInt(vkData.gamma[1][0]), BigInt(vkData.gamma[1][1]),
    BigInt(vkData.delta[0][0]), BigInt(vkData.delta[0][1]),
    BigInt(vkData.delta[1][0]), BigInt(vkData.delta[1][1]),
  ];

  const vkGammaABC = [];
  for (const point of vkData.gammaABC) {
    vkGammaABC.push(BigInt(point[0]));
    vkGammaABC.push(BigInt(point[1]));
  }

  return { vk, vkGammaABC };
}

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("\n============================================================");
  console.log("BTC Privacy Anchor — Ethereum Mainnet Deployment");
  console.log("============================================================");
  console.log("Deployer:", deployer.address);

  const balance = await deployer.provider.getBalance(deployer.address);
  const nativeCurrency = hre.network.name === "polygon" ? "MATIC/POL" : "ETH";
  console.log("Balance: ", hre.ethers.formatEther(balance), nativeCurrency);

  if (balance < hre.ethers.parseEther("0.02")) {
    console.error(`\nERROR: Need at least 0.02 ${nativeCurrency} for deployment.`);
    process.exit(1);
  }

  const { vk, vkGammaABC } = loadVerifyingKey();
  console.log("VK loaded — gammaABC entries:", vkGammaABC.length / 2);

  // Denomination: 1 wei (symbolic; the BTC_ANCHOR's job is to anchor
  // zkSNARK proofs for BTC orders — the 1 wei deposit is just a formality)
  const DENOMINATION = 1n;

  console.log("\nDeploying MiximusNative (BTC anchor)...");
  console.log("  denomination =", DENOMINATION.toString(), "wei");
  console.log("  symbol       = BTC_ANCHOR");

  const MiximusNative = await hre.ethers.getContractFactory("MiximusNative");
  const anchor = await MiximusNative.deploy(DENOMINATION, "BTC_ANCHOR", vk, vkGammaABC);

  await anchor.waitForDeployment();
  const address = await anchor.getAddress();

  const deployedBalance = await deployer.provider.getBalance(deployer.address);
  const ethUsed = hre.ethers.formatEther(balance - deployedBalance);

  console.log("\n============================================================");
  console.log("BTC Privacy Anchor deployed!");
  console.log("============================================================");
  console.log("Contract:  ", address);
  const explorerBase = hre.network.name === "polygon"
    ? "https://polygonscan.com/address/"
    : `https://${hre.network.name === "ethereum" ? "" : hre.network.name + "."}etherscan.io/address/`;

  console.log("Network:   ", hre.network.name);
  console.log("MATIC used:", ethUsed);
  console.log("Explorer:  ", `${explorerBase}${address}`);

  // ── Save address file ─────────────────────────────────────────────────────
  const outPath = path.join(__dirname, "btc-anchor-address-mainnet.json");
  const output = {
    network:             hre.network.name,
    btc_anchor_contract: address,
    denomination:        DENOMINATION.toString(),
    deployer:            deployer.address,
    deployed_at:         new Date().toISOString(),
  };
  fs.writeFileSync(outPath, JSON.stringify(output, null, 2));
  console.log("\nAddress saved to:", outPath);

  // ── Auto-patch config/assets.json ─────────────────────────────────────────
  const assetsPath = path.join(__dirname, "../../config/assets.json");
  if (fs.existsSync(assetsPath)) {
    const assets = JSON.parse(fs.readFileSync(assetsPath, "utf8"));
    const internalCoins = assets.assets.internal_coins || [];
    const existing = internalCoins.find(
      (a) => a.symbol === "BTC_ANCHOR" && a.chain === hre.network.name
    );
    if (existing) {
      existing.mixer_contract = address;
    } else {
      assets.assets.internal_coins = [
        ...internalCoins,
        {
          symbol:          "BTC_ANCHOR",
          name:            `BTC Privacy Anchor (${hre.network.name})`,
          chain:           hre.network.name,
          type:            "native",
          decimals:        18,
          denomination:    "1",
          mixer_contract:  address,
          _note:           "1 wei — internal zkSNARK notary for BTC mainnet orders; not user-selectable",
        },
      ];
    }
    fs.writeFileSync(assetsPath, JSON.stringify(assets, null, 2));
    console.log("config/assets.json updated with BTC_ANCHOR mainnet address.");
  }

  // ── Auto-patch seed_pools.py ──────────────────────────────────────────────
  const seedPoolsPath = path.join(__dirname, "../../webapp/backend/seed_pools.py");
  if (fs.existsSync(seedPoolsPath)) {
    let content = fs.readFileSync(seedPoolsPath, "utf8");
    // Replace the placeholder in the mainnet BTC_ANCHOR entry
    content = content.replace(
      /("mixer_contract":\s*")(0x<BTC_ANCHOR_MAINNET>)(")/,
      `$1${address}$3`
    );
    fs.writeFileSync(seedPoolsPath, content);
    console.log("webapp/backend/seed_pools.py updated with BTC_ANCHOR mainnet address.");
  }

  console.log("\n=== NEXT STEPS ===");
  console.log("1. Register pools in the database:");
  console.log("   cd webapp/backend && python seed_pools.py");
  console.log("   (note the mainnet BTC address printed — send BTC there to fund the pool)");
  console.log("2. Seed BTC_ANCHOR pool units (each unit = 1 wei deposit):");
  console.log("   python seed_units.py --symbol BTC_ANCHOR --chain ethereum --network-mode mainnet --units 20");
  console.log("3. Fund the service wallet with BTC (see address from step 1).");
  console.log("   Each order consumes 0.001 BTC — fund with however many units you want.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
