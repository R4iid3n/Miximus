/**
 * Deploy USDC Mixer to Sepolia using the real Sepolia USDC token.
 * Usage: npx hardhat run deployment/evm/deploy-usdc-sepolia.js --network sepolia
 */
const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

// Real Sepolia USDC (Circle official)
const USDC_ADDRESS = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238";

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
  console.log(`\nDeployer: ${deployer.address}`);
  console.log(`Balance:  ${hre.ethers.formatEther(balance)} ETH`);
  console.log(`USDC token: ${USDC_ADDRESS}\n`);

  const { vk, vkGammaABC } = loadVerifyingKey();

  // Deploy USDC Mixer pointing to real Sepolia USDC
  const denomination = 1000000n; // 1 USDC (6 decimals)
  console.log("Deploying MiximusERC20 (USDC, 1 USDC)...");
  const MiximusERC20 = await hre.ethers.getContractFactory("MiximusERC20");
  const mixer = await MiximusERC20.deploy(USDC_ADDRESS, denomination, "USDC", vk, vkGammaABC);
  await mixer.waitForDeployment();
  const addr = await mixer.getAddress();

  console.log(`\n  USDC Mixer: ${addr}`);
  console.log(`  USDC Token: ${USDC_ADDRESS}`);
  console.log(`  Explorer: https://sepolia.etherscan.io/address/${addr}\n`);
}

main().catch(console.error);
