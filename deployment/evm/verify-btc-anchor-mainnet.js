/**
 * Verify BTC Privacy Anchor Contract on Polygonscan
 *
 * Usage (from deployment/evm/ directory):
 *   npx hardhat run verify-btc-anchor-mainnet.js --network polygon
 */

const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

async function main() {
  const addrFile = path.join(__dirname, "btc-anchor-address-mainnet.json");
  if (!fs.existsSync(addrFile)) {
    throw new Error("btc-anchor-address-mainnet.json not found. Deploy first.");
  }
  const { btc_anchor_contract } = JSON.parse(fs.readFileSync(addrFile, "utf8"));

  const vkPath = path.join(__dirname, "../../ethsnarks-miximus/.keys/miximus.vk.json");
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

  console.log("Verifying BTC Privacy Anchor on Polygonscan...");
  console.log("Contract:", btc_anchor_contract);

  await hre.run("verify:verify", {
    address: btc_anchor_contract,
    constructorArguments: [1n, "BTC_ANCHOR", vk, vkGammaABC],
  });

  console.log("Verification submitted!");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
