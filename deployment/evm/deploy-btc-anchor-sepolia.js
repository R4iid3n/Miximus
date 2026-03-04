/**
 * Deploy BTC Privacy Anchor Contract (Sepolia)
 *
 * Deploys a MiximusNative contract with denomination = 1 wei.
 * This contract is NOT used for ETH mixing — it serves as a zkSNARK "notary"
 * for Bitcoin orders, publishing nullifiers on Ethereum to prove anonymity.
 *
 * Usage (from deployment/evm/ directory):
 *   npx hardhat run deploy-btc-anchor-sepolia.js --network sepolia
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

  // Flat array of 14 uint256 values:
  // [alpha_x, alpha_y, beta_x1, beta_y1, beta_x2, beta_y2,
  //  gamma_x1, gamma_y1, gamma_x2, gamma_y2,
  //  delta_x1, delta_y1, delta_x2, delta_y2]
  const vk = [
    BigInt(vkData.alpha[0]),    BigInt(vkData.alpha[1]),
    BigInt(vkData.beta[0][0]),  BigInt(vkData.beta[0][1]),
    BigInt(vkData.beta[1][0]),  BigInt(vkData.beta[1][1]),
    BigInt(vkData.gamma[0][0]), BigInt(vkData.gamma[0][1]),
    BigInt(vkData.gamma[1][0]), BigInt(vkData.gamma[1][1]),
    BigInt(vkData.delta[0][0]), BigInt(vkData.delta[0][1]),
    BigInt(vkData.delta[1][0]), BigInt(vkData.delta[1][1]),
  ];

  // Flat array: [x0, y0, x1, y1, ...]
  const vkGammaABC = [];
  for (const point of vkData.gammaABC) {
    vkGammaABC.push(BigInt(point[0]));
    vkGammaABC.push(BigInt(point[1]));
  }

  return { vk, vkGammaABC };
}

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Deploying BTC privacy anchor with account:", deployer.address);

  const balance = await deployer.provider.getBalance(deployer.address);
  console.log("Account balance:", hre.ethers.formatEther(balance), "ETH");

  const { vk, vkGammaABC } = loadVerifyingKey();
  console.log("VK loaded — gammaABC entries:", vkGammaABC.length / 2);

  // Denomination: 1 wei (symbolic; only gas cost matters)
  const DENOMINATION = 1n;

  console.log("\nDeploying MiximusNative (BTC anchor)...");
  console.log("  denomination =", DENOMINATION.toString(), "wei");

  // Constructor: (uint256 _denomination, string _assetSymbol, uint256[14] _vk, uint256[] _vkGammaABC)
  const MiximusNative = await hre.ethers.getContractFactory("MiximusNative");
  const anchor = await MiximusNative.deploy(DENOMINATION, "BTC_ANCHOR", vk, vkGammaABC);

  await anchor.waitForDeployment();
  const address = await anchor.getAddress();

  console.log("\nBTC Privacy Anchor deployed at:", address);
  console.log("Network:", hre.network.name);
  console.log("Explorer:", `https://sepolia.etherscan.io/address/${address}`);

  // Save address for easy reference
  const output = {
    network: hre.network.name,
    btc_anchor_contract: address,
    denomination: DENOMINATION.toString(),
    deployer: deployer.address,
    deployed_at: new Date().toISOString(),
  };
  const outPath = path.join(__dirname, "btc-anchor-address.json");
  fs.writeFileSync(outPath, JSON.stringify(output, null, 2));
  console.log("\nAddress saved to:", outPath);

  console.log("\n=== NEXT STEPS ===");
  console.log("1. Update mixer_contract in config/assets_testnet.json:");
  console.log(`   "mixer_contract": "${address}"`);
  console.log("2. Update mixer_contract in webapp/backend/seed_pools.py:");
  console.log(`   "mixer_contract": "${address}"`);
  console.log("3. Re-run seed_pools.py, then seed anchor units:");
  console.log("   python seed_units.py --symbol BTC_ANCHOR --chain ethereum --network-mode testnet --units 10");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
