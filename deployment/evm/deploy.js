/**
 * Multi-Chain EVM Deployment Script
 *
 * Deploys the MiximusFactory and creates mixer pools for native currency
 * and all relevant ERC20/BEP20 tokens on the target chain.
 *
 * Usage:
 *   npx hardhat run deploy.js --network ethereum
 *   npx hardhat run deploy.js --network bsc
 *   npx hardhat run deploy.js --network polygon
 *
 * The script:
 *   1. Deploys MiximusFactory
 *   2. Creates a native currency pool (ETH/BNB/MATIC/AVAX/etc.)
 *   3. Creates ERC20 token pools for all tokens on that chain
 */

const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

// Load asset configuration — pick testnet config for testnet networks
const TESTNET_NETWORKS = ["bsc_testnet", "sepolia", "goerli", "polygon_mumbai", "polygon_amoy", "avalanche_fuji", "arbitrum_sepolia", "base_sepolia", "optimism_sepolia", "holesky"];
function loadAssetsConfig(networkName) {
  const isTestnet = TESTNET_NETWORKS.includes(networkName);
  const configFile = isTestnet ? "assets_testnet.json" : "assets.json";
  const configPath = path.join(__dirname, "../../config", configFile);
  console.log(`   Loading ${configFile} (${isTestnet ? "testnet" : "mainnet"} mode)`);
  return JSON.parse(fs.readFileSync(configPath, "utf8"));
}

// Load verifying key from generated keys
function loadVerifyingKey() {
  const vkPath = path.join(__dirname, "../../ethsnarks-miximus/.keys/miximus.vk.json");
  try {
    const vkData = JSON.parse(fs.readFileSync(vkPath, "utf8"));
    // VK format: alpha(2) + beta(4) + gamma(4) + delta(4) = 14 uint256 values
    const vk = [
      BigInt(vkData.alpha[0]),  BigInt(vkData.alpha[1]),           // alpha (G1)
      BigInt(vkData.beta[0][0]),  BigInt(vkData.beta[0][1]),       // beta (G2)
      BigInt(vkData.beta[1][0]),  BigInt(vkData.beta[1][1]),
      BigInt(vkData.gamma[0][0]), BigInt(vkData.gamma[0][1]),      // gamma (G2)
      BigInt(vkData.gamma[1][0]), BigInt(vkData.gamma[1][1]),
      BigInt(vkData.delta[0][0]), BigInt(vkData.delta[0][1]),      // delta (G2)
      BigInt(vkData.delta[1][0]), BigInt(vkData.delta[1][1]),
    ];
    // GammaABC: flat array of [x0, y0, x1, y1, ...]
    const vkGammaABC = [];
    for (const point of vkData.gammaABC) {
      vkGammaABC.push(BigInt(point[0]));
      vkGammaABC.push(BigInt(point[1]));
    }
    console.log(`   Loaded real verifying key (${vk.length} VK values, ${vkGammaABC.length} gammaABC values)`);
    return { vk, vkGammaABC };
  } catch (e) {
    console.log(`   Warning: Could not load VK from ${vkPath}: ${e.message}`);
    console.log("   Using placeholder verifying key.");
    return {
      vk: Array(14).fill(BigInt("0x0000000000000000000000000000000000000000000000000000000000000001")),
      vkGammaABC: [BigInt(1), BigInt(2), BigInt(3), BigInt(4)],
    };
  }
}

const { vk: VERIFYING_KEY, vkGammaABC: VK_GAMMA_ABC } = loadVerifyingKey();

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const network = hre.network.name;
  const chainId = (await hre.ethers.provider.getNetwork()).chainId;
  const assetsConfig = loadAssetsConfig(network);

  console.log(`\n${"=".repeat(60)}`);
  console.log(`Deploying Miximus to ${network} (chainId: ${chainId})`);
  console.log(`Deployer: ${deployer.address}`);
  console.log(`Balance: ${hre.ethers.formatEther(await deployer.provider.getBalance(deployer.address))} native`);
  console.log(`${"=".repeat(60)}\n`);

  // 1. Deploy Factory
  console.log("1. Deploying MiximusFactory...");
  const Factory = await hre.ethers.getContractFactory("MiximusFactory");
  const factory = await Factory.deploy();
  await factory.waitForDeployment();
  const factoryAddr = await factory.getAddress();
  console.log(`   Factory deployed: ${factoryAddr}\n`);

  // 2. Find matching chain in config
  const chainConfig = Object.entries(assetsConfig.chains).find(
    ([_, cfg]) => cfg.chain_id === Number(chainId)
  );

  if (!chainConfig) {
    console.log(`Warning: No chain config found for chainId ${chainId}`);
    console.log("Deploying with default settings.\n");
  }

  const [chainKey, chainCfg] = chainConfig || [network, {}];
  const nativeSymbol = chainCfg.native_currency || "ETH";
  const nativeDecimals = chainCfg.native_decimals || 18;

  // 3. Create native currency pool
  console.log(`2. Creating native currency pool (${nativeSymbol})...`);
  const nativeDenomination = hre.ethers.parseUnits("1", nativeDecimals);
  const nativePoolTx = await factory.createNativePool(
    nativeDenomination,
    nativeSymbol,
    VERIFYING_KEY,
    VK_GAMMA_ABC
  );
  const nativeReceipt = await nativePoolTx.wait();
  const nativePoolEvent = nativeReceipt.logs.find(l => l.fragment?.name === "NativePoolCreated");
  const nativePoolAddr = nativePoolEvent?.args?.[0] || "unknown";
  console.log(`   ${nativeSymbol} pool: ${nativePoolAddr}\n`);

  // 4. Find all ERC20 tokens for this chain
  const allAssets = [
    ...assetsConfig.assets.stablecoins,
    ...assetsConfig.assets.wrapped_assets,
    ...assetsConfig.assets.defi_tokens,
    ...(assetsConfig.assets.exchange_network_tokens || []),
  ];

  const chainTokens = allAssets.filter(
    (a) => a.chain === chainKey && a.contract && a.type !== "native"
  );

  console.log(`3. Creating ${chainTokens.length} token pools...\n`);

  const deployedPools = {
    network,
    chainId: Number(chainId),
    factory: factoryAddr,
    nativePool: { symbol: nativeSymbol, address: nativePoolAddr },
    tokenPools: [],
  };

  for (const token of chainTokens) {
    try {
      console.log(`   Deploying ${token.symbol} pool (${token.contract})...`);
      const tokenDenom = BigInt(token.denomination);

      const tx = await factory.createERC20Pool(
        token.contract,
        tokenDenom,
        token.symbol,
        VERIFYING_KEY,
        VK_GAMMA_ABC
      );
      const receipt = await tx.wait();
      const event = receipt.logs.find(l => l.fragment?.name === "ERC20PoolCreated");
      const poolAddr = event?.args?.[0] || "unknown";

      console.log(`   ✓ ${token.symbol}: ${poolAddr}`);
      deployedPools.tokenPools.push({
        symbol: token.symbol,
        name: token.name,
        tokenContract: token.contract,
        poolAddress: poolAddr,
        denomination: token.denomination,
      });
    } catch (e) {
      console.log(`   ✗ ${token.symbol}: ${e.message}`);
    }
  }

  // 5. Save deployment addresses
  const outputPath = path.join(__dirname, `deployments-${network}.json`);
  fs.writeFileSync(outputPath, JSON.stringify(deployedPools, null, 2));
  console.log(`\nDeployment addresses saved to: ${outputPath}`);

  // Summary
  console.log(`\n${"=".repeat(60)}`);
  console.log(`Deployment Summary:`);
  console.log(`  Factory:     ${factoryAddr}`);
  console.log(`  Native pool: ${nativePoolAddr} (${nativeSymbol})`);
  console.log(`  Token pools: ${deployedPools.tokenPools.length}`);
  console.log(`${"=".repeat(60)}\n`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
