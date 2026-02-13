/**
 * Verify all Sepolia contracts on Etherscan.
 * Usage: npx hardhat run deployment/evm/verify-sepolia.js --network sepolia
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

async function verify(address, contractName, constructorArgs) {
  console.log(`\nVerifying ${contractName} at ${address}...`);
  try {
    await hre.run("verify:verify", {
      address,
      constructorArguments: constructorArgs,
    });
    console.log(`  ✓ ${contractName} verified!`);
  } catch (e) {
    if (e.message.includes("Already Verified") || e.message.includes("already verified")) {
      console.log(`  ✓ ${contractName} already verified.`);
    } else {
      console.log(`  ✗ ${contractName} failed: ${e.message}`);
    }
  }
}

async function main() {
  const { vk, vkGammaABC } = loadVerifyingKey();

  const denomination_ETH = BigInt("60000000000000000"); // 0.06 ETH
  const denomination_STABLE = BigInt("1000000");          // 1 token (6 dec)

  console.log("============================================================");
  console.log("Verifying Sepolia Contracts on Etherscan");
  console.log("============================================================");

  // 1. TestERC20 (USDT)
  await verify(
    "0x82cd1ECEebA7FcFB60191bCB18AA9F259AB52495",
    "TestERC20 (USDT)",
    ["Test Tether", "USDT", 6]
  );

  // 2. TestERC20 (USDC)
  await verify(
    "0xaDa8A85c68E49153FB114C0eE1b165B3B46a9611",
    "TestERC20 (USDC)",
    ["Test USD Coin", "USDC", 6]
  );

  // 3. MiximusNative (ETH)
  await verify(
    "0x85A4ecCe24580f6d90adFFD74d9B061BD3A4f3c4",
    "MiximusNative (ETH)",
    [denomination_ETH, "ETH", vk, vkGammaABC]
  );

  // 4. MiximusERC20 (USDT)
  await verify(
    "0x7a958DBd4C3BDd7ff82ed3ffab5e895a8b49C4EA",
    "MiximusERC20 (USDT)",
    ["0x82cd1ECEebA7FcFB60191bCB18AA9F259AB52495", denomination_STABLE, "USDT", vk, vkGammaABC]
  );

  // 5. MiximusERC20 (USDC)
  await verify(
    "0xBeB1B7eA73e18fA7E588f09C9154F2781E48578b",
    "MiximusERC20 (USDC)",
    ["0xaDa8A85c68E49153FB114C0eE1b165B3B46a9611", denomination_STABLE, "USDC", vk, vkGammaABC]
  );

  console.log("\n============================================================");
  console.log("Verification complete!");
  console.log("============================================================");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
