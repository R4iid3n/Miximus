/**
 * Deploy remaining Sepolia contracts (USDT mixer + USDC token + USDC mixer)
 * MiximusNative (ETH) already deployed at 0x85A4ecCe24580f6d90adFFD74d9B061BD3A4f3c4
 * TestUSDT already deployed at 0x82cd1ECEebA7FcFB60191bCB18AA9F259AB52495
 *
 * Usage: npx hardhat run deployment/evm/deploy-sepolia-remaining.js --network sepolia
 */

const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

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

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const balance = await deployer.provider.getBalance(deployer.address);

  console.log("\n============================================================");
  console.log("Deploying Remaining Sepolia Contracts");
  console.log(`Deployer: ${deployer.address}`);
  console.log(`Balance:  ${hre.ethers.formatEther(balance)} ETH`);
  console.log("============================================================\n");

  const { vk, vkGammaABC } = loadVerifyingKey();
  console.log(`VK loaded: ${vk.length} values, gammaABC: ${vkGammaABC.length} values\n`);

  // Already deployed
  const USDT_TOKEN = "0x82cd1ECEebA7FcFB60191bCB18AA9F259AB52495";
  const ETH_MIXER = "0x85A4ecCe24580f6d90adFFD74d9B061BD3A4f3c4";

  const denomination = 1000000n; // 1 token (6 decimals)

  // ─── 1. Deploy USDT Mixer ─────────────────────────────────────────────────
  console.log("1/3 Deploying MiximusERC20 for USDT...");
  const MiximusERC20 = await hre.ethers.getContractFactory("MiximusERC20");
  const usdtMixer = await MiximusERC20.deploy(USDT_TOKEN, denomination, "USDT", vk, vkGammaABC);
  await usdtMixer.waitForDeployment();
  const usdtMixerAddr = await usdtMixer.getAddress();
  console.log(`    USDT Mixer: ${usdtMixerAddr}`);
  console.log(`    Explorer: https://sepolia.etherscan.io/address/${usdtMixerAddr}\n`);

  // Wait for nonce to propagate
  console.log("  Waiting 10s for nonce sync...");
  await sleep(10000);

  // ─── 2. Deploy Test USDC Token ────────────────────────────────────────────
  console.log("2/3 Deploying test USDC token...");
  const TestToken = await hre.ethers.getContractFactory("TestERC20");
  const usdcToken = await TestToken.deploy("Test USD Coin", "USDC", 6);
  await usdcToken.waitForDeployment();
  const usdcTokenAddr = await usdcToken.getAddress();

  // Mint 1000 test USDC
  const mintTx = await usdcToken.mint(deployer.address, 1000n * 10n ** 6n);
  await mintTx.wait();
  console.log(`    USDC Token: ${usdcTokenAddr}`);
  console.log(`    Minted 1000 USDC to deployer\n`);

  console.log("  Waiting 10s for nonce sync...");
  await sleep(10000);

  // ─── 3. Deploy USDC Mixer ─────────────────────────────────────────────────
  console.log("3/3 Deploying MiximusERC20 for USDC...");
  const usdcMixer = await MiximusERC20.deploy(usdcTokenAddr, denomination, "USDC", vk, vkGammaABC);
  await usdcMixer.waitForDeployment();
  const usdcMixerAddr = await usdcMixer.getAddress();
  console.log(`    USDC Mixer: ${usdcMixerAddr}`);
  console.log(`    Explorer: https://sepolia.etherscan.io/address/${usdcMixerAddr}\n`);

  // ─── Save all deployment info ─────────────────────────────────────────────
  const deploymentInfo = {
    network: "sepolia",
    chainId: 11155111,
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
    contracts: {
      ETH: {
        contract: "MiximusNative",
        address: ETH_MIXER,
        denomination: "0.06 ETH",
        denominationWei: hre.ethers.parseEther("0.06").toString(),
        symbol: "ETH",
        type: "native",
      },
      USDT: {
        contract: "MiximusERC20",
        address: usdtMixerAddr,
        tokenAddress: USDT_TOKEN,
        denomination: "1 USDT",
        denominationWei: denomination.toString(),
        symbol: "USDT",
        type: "erc20",
      },
      USDC: {
        contract: "MiximusERC20",
        address: usdcMixerAddr,
        tokenAddress: usdcTokenAddr,
        denomination: "1 USDC",
        denominationWei: denomination.toString(),
        symbol: "USDC",
        type: "erc20",
      },
    },
    tokens: {
      USDT: USDT_TOKEN,
      USDC: usdcTokenAddr,
    },
  };

  const outputPath = path.join(__dirname, "deployments-sepolia.json");
  fs.writeFileSync(outputPath, JSON.stringify(deploymentInfo, null, 2));
  console.log(`Saved to: ${outputPath}`);

  console.log("\n============================================================");
  console.log("ALL SEPOLIA DEPLOYMENTS COMPLETE");
  console.log("============================================================");
  console.log(`  ETH Mixer:   ${ETH_MIXER}`);
  console.log(`  USDT Token:  ${USDT_TOKEN}`);
  console.log(`  USDT Mixer:  ${usdtMixerAddr}`);
  console.log(`  USDC Token:  ${usdcTokenAddr}`);
  console.log(`  USDC Mixer:  ${usdcMixerAddr}`);
  console.log("============================================================\n");

  const remaining = await deployer.provider.getBalance(deployer.address);
  console.log(`Remaining: ${hre.ethers.formatEther(remaining)} ETH`);
  console.log(`Gas used:  ${hre.ethers.formatEther(balance - remaining)} ETH`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
