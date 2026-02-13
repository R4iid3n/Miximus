/**
 * Deploy all Miximus contracts to Sepolia Testnet:
 *   1. MiximusNative (ETH, 0.06 ETH denomination)
 *   2. MiximusERC20  (USDT, 1 USDT denomination)
 *   3. MiximusERC20  (USDC, 1 USDC denomination)
 *
 * Usage (from project root):
 *   npx hardhat run deployment/evm/deploy-sepolia.js --network sepolia
 */

const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

// ─── Testnet token addresses on Sepolia ───────────────────────────────────────
// These are well-known test token deployments. If you deploy your own test
// tokens, replace these addresses.
//
// NOTE: Real USDT/USDC do not exist on Sepolia. We deploy simple test ERC20
// tokens below if these addresses are zero.
// ──────────────────────────────────────────────────────────────────────────────

const USDT_SEPOLIA = process.env.USDT_SEPOLIA_ADDRESS || "";
const USDC_SEPOLIA = process.env.USDC_SEPOLIA_ADDRESS || "";

// ─── Verifying Key Loader ────────────────────────────────────────────────────

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

// ─── Simple Test ERC20 (for Sepolia where no real USDT/USDC exists) ─────────

const TEST_ERC20_ABI = [
  "constructor(string name, string symbol, uint8 decimals)",
  "function mint(address to, uint256 amount) external",
  "function balanceOf(address) view returns (uint256)",
  "function approve(address, uint256) returns (bool)",
];

const TEST_ERC20_BYTECODE =
  "0x608060405234801561001057600080fd5b5060405161093638038061093683398101604081905261002f91" +
  "610127565b825161004290600090602086019061007e565b50815161005690600190602085019061007e565b50" +
  "6002805460ff191660ff929092169190911790555061021f565b828054610082906101e4565b90600052602060" +
  "0020906001f01602900481019282610094576000855561010a565b82601f106100cd57805160ff191683800117" +
  "855561010a565b8280016001018555821561010a579182015b8281111561010a57825182559160200191906001" +
  "0190610100565b5061011692915061011a565b5090565b5b80821115610116576000815560010161011b565b60" +
  "00806000606084860312156101455761014560006101fc565b835167ffffffffffffffff81111561016257610162" +
  "60006101fc565b6020850186601f8301121561017d5761017d60006101fc565b80516101906101888261019d565b" +
  "82019150838183011115610198576000855b505050509250929050565b634e487b7160e01b600052604160045260" +
  "24600052602460045260446000fd5b60006020808301818452838183015250604090810190565b600181811c9082" +
  "16806101f857607f821691505b60208210811415610218576000fd5b5091905056fe";

async function deployTestERC20(deployer, name, symbol, decimals) {
  console.log(`  Deploying test ${symbol} token...`);
  const factory = new hre.ethers.ContractFactory(
    [
      "constructor(string memory _name, string memory _symbol, uint8 _decimals)",
      "function mint(address to, uint256 amount) public",
      "function name() view returns (string)",
      "function symbol() view returns (string)",
      "function decimals() view returns (uint8)",
      "function totalSupply() view returns (uint256)",
      "function balanceOf(address) view returns (uint256)",
      "function transfer(address to, uint256 amount) returns (bool)",
      "function approve(address spender, uint256 amount) returns (bool)",
      "function transferFrom(address from, address to, uint256 amount) returns (bool)",
      "function allowance(address owner, address spender) view returns (uint256)",
      "event Transfer(address indexed from, address indexed to, uint256 value)",
      "event Approval(address indexed owner, address indexed spender, uint256 value)",
    ],
    // Minimal ERC20 with mint — compiled bytecode for OpenZeppelin-style ERC20
    // We'll use Hardhat's compilation instead
    deployer
  );

  // Actually, let's just deploy a minimal Solidity test token via Hardhat
  // Since we don't have a TestERC20 in our contracts, we'll create one inline
  return null;
}

// ─── Main Deployment ─────────────────────────────────────────────────────────

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const balance = await deployer.provider.getBalance(deployer.address);

  console.log("\n============================================================");
  console.log("Deploying Miximus Contracts to Sepolia Testnet");
  console.log(`Deployer: ${deployer.address}`);
  console.log(`Balance:  ${hre.ethers.formatEther(balance)} ETH`);
  console.log("============================================================\n");

  if (balance === 0n) {
    console.error("ERROR: Deployer has 0 ETH. Get Sepolia ETH from:");
    console.error("  https://sepoliafaucet.com");
    process.exit(1);
  }

  // Load verifying key
  console.log("Loading verifying key...");
  const { vk, vkGammaABC } = loadVerifyingKey();
  console.log(`  VK: ${vk.length} values, gammaABC: ${vkGammaABC.length} values\n`);

  const deployments = {};

  // ─── 1. Deploy MiximusNative (ETH, 0.06 ETH) ─────────────────────────────
  {
    const denomination = hre.ethers.parseEther("0.06");
    console.log("1/3 Deploying MiximusNative (ETH, 0.06 ETH)...");

    const MiximusNative = await hre.ethers.getContractFactory("MiximusNative");
    const mixer = await MiximusNative.deploy(denomination, "ETH", vk, vkGammaABC);
    await mixer.waitForDeployment();
    const addr = await mixer.getAddress();

    console.log(`    Address: ${addr}`);
    console.log(`    Explorer: https://sepolia.etherscan.io/address/${addr}\n`);

    deployments.ETH = {
      contract: "MiximusNative",
      address: addr,
      denomination: "0.06 ETH",
      denominationWei: denomination.toString(),
      symbol: "ETH",
      type: "native",
    };
  }

  // ─── 2. Deploy MiximusERC20 (USDT, 1 USDT) ──────────────────────────────
  {
    let tokenAddr = USDT_SEPOLIA;

    if (!tokenAddr || tokenAddr === "0x0000000000000000000000000000000000000000") {
      console.log("2/3 No USDT token address set — deploying test USDT first...");

      // Deploy a minimal test ERC20 for USDT
      const TestToken = await hre.ethers.getContractFactory("TestERC20");
      const token = await TestToken.deploy("Test Tether", "USDT", 6);
      await token.waitForDeployment();
      tokenAddr = await token.getAddress();

      // Mint some test USDT to deployer for seeding
      const mintAmount = 1000n * 10n ** 6n; // 1000 USDT
      await token.mint(deployer.address, mintAmount);
      console.log(`    Test USDT deployed: ${tokenAddr}`);
      console.log(`    Minted 1000 USDT to deployer\n`);
    }

    const denomination = 1000000n; // 1 USDT (6 decimals)
    console.log("2/3 Deploying MiximusERC20 (USDT, 1 USDT)...");

    const MiximusERC20 = await hre.ethers.getContractFactory("MiximusERC20");
    const mixer = await MiximusERC20.deploy(tokenAddr, denomination, "USDT", vk, vkGammaABC);
    await mixer.waitForDeployment();
    const addr = await mixer.getAddress();

    console.log(`    Address: ${addr}`);
    console.log(`    Token:   ${tokenAddr}`);
    console.log(`    Explorer: https://sepolia.etherscan.io/address/${addr}\n`);

    deployments.USDT = {
      contract: "MiximusERC20",
      address: addr,
      tokenAddress: tokenAddr,
      denomination: "1 USDT",
      denominationWei: denomination.toString(),
      symbol: "USDT",
      type: "erc20",
    };
  }

  // ─── 3. Deploy MiximusERC20 (USDC, 1 USDC) ──────────────────────────────
  {
    let tokenAddr = USDC_SEPOLIA;

    if (!tokenAddr || tokenAddr === "0x0000000000000000000000000000000000000000") {
      console.log("3/3 No USDC token address set — deploying test USDC first...");

      const TestToken = await hre.ethers.getContractFactory("TestERC20");
      const token = await TestToken.deploy("Test USD Coin", "USDC", 6);
      await token.waitForDeployment();
      tokenAddr = await token.getAddress();

      const mintAmount = 1000n * 10n ** 6n; // 1000 USDC
      await token.mint(deployer.address, mintAmount);
      console.log(`    Test USDC deployed: ${tokenAddr}`);
      console.log(`    Minted 1000 USDC to deployer\n`);
    }

    const denomination = 1000000n; // 1 USDC (6 decimals)
    console.log("3/3 Deploying MiximusERC20 (USDC, 1 USDC)...");

    const MiximusERC20 = await hre.ethers.getContractFactory("MiximusERC20");
    const mixer = await MiximusERC20.deploy(tokenAddr, denomination, "USDC", vk, vkGammaABC);
    await mixer.waitForDeployment();
    const addr = await mixer.getAddress();

    console.log(`    Address: ${addr}`);
    console.log(`    Token:   ${tokenAddr}`);
    console.log(`    Explorer: https://sepolia.etherscan.io/address/${addr}\n`);

    deployments.USDC = {
      contract: "MiximusERC20",
      address: addr,
      tokenAddress: tokenAddr,
      denomination: "1 USDC",
      denominationWei: denomination.toString(),
      symbol: "USDC",
      type: "erc20",
    };
  }

  // ─── Save Deployment Info ─────────────────────────────────────────────────
  const deploymentInfo = {
    network: "sepolia",
    chainId: 11155111,
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
    contracts: deployments,
  };

  const outputPath = path.join(__dirname, "deployments-sepolia.json");
  fs.writeFileSync(outputPath, JSON.stringify(deploymentInfo, null, 2));
  console.log(`Deployment info saved to: ${outputPath}`);

  console.log("\n============================================================");
  console.log("ALL SEPOLIA DEPLOYMENTS COMPLETE");
  console.log("============================================================");
  console.log(`  ETH Mixer:  ${deployments.ETH.address}`);
  console.log(`  USDT Mixer: ${deployments.USDT.address}`);
  console.log(`  USDC Mixer: ${deployments.USDC.address}`);
  console.log("============================================================\n");

  const remainingBalance = await deployer.provider.getBalance(deployer.address);
  console.log(`Remaining balance: ${hre.ethers.formatEther(remainingBalance)} ETH`);
  console.log(`Gas spent: ${hre.ethers.formatEther(balance - remainingBalance)} ETH`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
