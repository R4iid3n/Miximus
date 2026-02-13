/**
 * Deploy MiximusNative directly to BSC Testnet
 *
 * Usage (from project root):
 *   npx hardhat run deployment/evm/deploy-bsc-testnet.js --network bsc_testnet
 */

const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

// Load verifying key
function loadVerifyingKey() {
  const vkPath = path.join(__dirname, "../../ethsnarks-miximus/.keys/miximus.vk.json");
  const vkData = JSON.parse(fs.readFileSync(vkPath, "utf8"));

  const vk = [
    BigInt(vkData.alpha[0]),  BigInt(vkData.alpha[1]),
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
  const balance = await deployer.provider.getBalance(deployer.address);

  console.log("\n============================================================");
  console.log("Deploying MiximusNative to BSC Testnet");
  console.log(`Deployer: ${deployer.address}`);
  console.log(`Balance:  ${hre.ethers.formatEther(balance)} BNB`);
  console.log("============================================================\n");

  if (balance === 0n) {
    console.error("ERROR: Deployer has 0 BNB. Get testnet BNB from:");
    console.error("  https://www.bnbchain.org/en/testnet-faucet");
    process.exit(1);
  }

  // Load the real verifying key
  console.log("Loading verifying key...");
  const { vk, vkGammaABC } = loadVerifyingKey();
  console.log(`  VK: ${vk.length} values, gammaABC: ${vkGammaABC.length} values\n`);

  // Deploy MiximusNative with 0.01 BNB denomination (testnet-friendly)
  const denomination = hre.ethers.parseEther("0.01"); // 0.01 BNB for testing
  const assetSymbol = "BNB";

  console.log(`Deploying MiximusNative (${assetSymbol}, denomination: 0.01 BNB)...`);
  const MiximusNative = await hre.ethers.getContractFactory("MiximusNative");
  const mixer = await MiximusNative.deploy(denomination, assetSymbol, vk, vkGammaABC);
  await mixer.waitForDeployment();
  const mixerAddr = await mixer.getAddress();

  console.log(`\nMiximusNative deployed at: ${mixerAddr}`);
  console.log(`Explorer: https://testnet.bscscan.com/address/${mixerAddr}`);

  // Save deployment info
  const deploymentInfo = {
    network: "bsc_testnet",
    chainId: 97,
    contract: "MiximusNative",
    address: mixerAddr,
    denomination: "0.01",
    denominationWei: denomination.toString(),
    assetSymbol: "BNB",
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
    verifyingKey: {
      vkCount: vk.length,
      gammaABCCount: vkGammaABC.length,
    },
  };

  const outputPath = path.join(__dirname, "deployments-bsc_testnet.json");
  fs.writeFileSync(outputPath, JSON.stringify(deploymentInfo, null, 2));
  console.log(`\nDeployment info saved to: ${outputPath}`);

  console.log("\n============================================================");
  console.log("DEPLOYMENT COMPLETE");
  console.log(`  Contract: ${mixerAddr}`);
  console.log(`  Denomination: 0.01 BNB`);
  console.log(`  Next: Update config/assets_testnet.json with this address`);
  console.log("============================================================\n");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
