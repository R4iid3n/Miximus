/**
 * Verify that our Solidity MiMC matches the ethsnarks C++ circuit's MiMC.
 * Uses known test vectors from the ethsnarks Python test suite.
 */
const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

function loadVK() {
  const vkPath = path.join(__dirname, "../ethsnarks-miximus/.keys/miximus.vk.json");
  const vkData = JSON.parse(fs.readFileSync(vkPath, "utf8"));
  const vk = [
    BigInt(vkData.alpha[0]), BigInt(vkData.alpha[1]),
    BigInt(vkData.beta[0][0]), BigInt(vkData.beta[0][1]),
    BigInt(vkData.beta[1][0]), BigInt(vkData.beta[1][1]),
    BigInt(vkData.gamma[0][0]), BigInt(vkData.gamma[0][1]),
    BigInt(vkData.gamma[1][0]), BigInt(vkData.gamma[1][1]),
    BigInt(vkData.delta[0][0]), BigInt(vkData.delta[0][1]),
    BigInt(vkData.delta[1][0]), BigInt(vkData.delta[1][1]),
  ];
  const vkGammaABC = [];
  for (const pt of vkData.gammaABC) {
    vkGammaABC.push(BigInt(pt[0]));
    vkGammaABC.push(BigInt(pt[1]));
  }
  return { vk, vkGammaABC };
}

async function main() {
  const { vk, vkGammaABC } = loadVK();

  // Deploy a test pool
  const Factory = await ethers.getContractFactory("MiximusFactory");
  const factory = await Factory.deploy();
  await factory.waitForDeployment();

  const denom = ethers.parseEther("1");
  const tx = await factory.createNativePool(denom, "ETH", vk, vkGammaABC);
  const receipt = await tx.wait();
  const event = receipt.logs.find(l => l.fragment?.name === "NativePoolCreated");
  const pool = await ethers.getContractAt("MiximusNative", event.args[0]);

  console.log("=== MiMC Cross-Implementation Verification ===\n");

  // Test vector from ethsnarks: mimc_hash([1, 1]) with key=0
  // Expected: 4087330248547221366577133490880315793780387749595119806283278576811074525767
  const hash_1_1 = await pool.mimcHash([1n, 1n]);
  const expected_hash_1_1 = 4087330248547221366577133490880315793780387749595119806283278576811074525767n;
  console.log(`mimcHash([1, 1]):`);
  console.log(`  Solidity : ${hash_1_1}`);
  console.log(`  Expected : ${expected_hash_1_1}`);
  console.log(`  Match    : ${hash_1_1 === expected_hash_1_1 ? "YES" : "NO"}\n`);

  // Test: mimcHash([0]) - leaf hash of secret=0
  const hash_0 = await pool.mimcHash([0n]);
  console.log(`mimcHash([0]): ${hash_0}`);

  // Test: makeLeafHash(secret=1)
  const leaf_1 = await pool.makeLeafHash(1n);
  const hash_single_1 = await pool.mimcHash([1n]);
  console.log(`makeLeafHash(1): ${leaf_1}`);
  console.log(`mimcHash([1])  : ${hash_single_1}`);
  console.log(`Match: ${leaf_1 === hash_single_1 ? "YES" : "NO"}\n`);

  // Test with larger values from ethsnarks test suite
  const m0 = 3703141493535563179657531719960160174296085208671919316200479060314459804651n;
  const m1 = 134551314051432487569247388144051420116740427803855572138106146683954151557n;
  const k_val = 918403109389145570117360101535982733651217667914747213867238065296420114726n;

  // mimc_hash([m0, m1], k=k_val) should = 15683951496311901749339509118960676303290224812129752890706581988986633412003
  // Note: our mimcHash uses k=0, so we can't directly test with k!=0 via the public interface.
  // But we can test: mimc_hash([m0, m1], k=0)
  const hash_m0_m1 = await pool.mimcHash([m0, m1]);
  console.log(`mimcHash([m0, m1]) = ${hash_m0_m1}`);

  // Also verify the empty tree root is deterministic
  const root = await pool.getRoot();
  console.log(`\nEmpty tree root: ${root}`);

  if (hash_1_1 === expected_hash_1_1) {
    console.log("\n=== MiMC implementation MATCHES ethsnarks circuit ===");
  } else {
    console.log("\n=== WARNING: MiMC MISMATCH - proofs will NOT verify ===");
  }
}

main().catch(console.error);
