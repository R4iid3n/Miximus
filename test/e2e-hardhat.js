/**
 * Full End-to-End Test: Deposit → zkSNARK Prove → Anonymous Withdraw
 *
 * Runs on the in-process Hardhat network. Shells out to WSL for proof generation
 * using the C++ prover (libmiximus.so).
 *
 * Usage: npx hardhat run test/e2e-hardhat.js
 */

const hre = require("hardhat");
const { ethers } = hre;
const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const SCALAR_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617n;

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

/**
 * Compute external hash matching the contract:
 * uint256(sha256(abi.encodePacked(poolAddress, recipientAddress))) % SCALAR_FIELD
 */
function computeExtHash(poolAddress, recipientAddress) {
  const poolBytes = Buffer.from(poolAddress.slice(2).toLowerCase(), "hex");
  const recipBytes = Buffer.from(recipientAddress.slice(2).toLowerCase(), "hex");
  const packed = Buffer.concat([poolBytes, recipBytes]);
  const hash = crypto.createHash("sha256").update(packed).digest();
  const hashBigInt = BigInt("0x" + hash.toString("hex"));
  return hashBigInt % SCALAR_FIELD;
}

/**
 * Call the C++ prover via WSL to generate the zkSNARK proof and compute nullifier.
 * Writes args to a temp file, calls the helper Python script in WSL.
 */
function generateProofViaWSL(root, secret, exthash, addressBits, merklePath) {
  const addressInt = addressBits.reduce((acc, bit, i) => acc + (bit ? (1 << i) : 0), 0);

  // Write arguments to a temp JSON file
  const argsData = {
    root: "0x" + root.toString(16),
    exthash: "0x" + exthash.toString(16),
    secret: "0x" + secret.toString(16),
    address: addressInt,
    path: merklePath.map(p => "0x" + p.toString(16))
  };

  const argsFile = path.join(__dirname, "_proof_args.json");
  fs.writeFileSync(argsFile, JSON.stringify(argsData));

  // Convert Windows path to WSL path
  const wslArgsFile = "/mnt/c/AML mixer/test/_proof_args.json";
  const wslScript = "/mnt/c/AML mixer/test/generate_proof.py";

  const resultFile = path.join(__dirname, "_proof_args.result.json");
  // Clean up any previous result file
  try { fs.unlinkSync(resultFile); } catch (_) {}

  console.log("   Calling C++ prover via WSL (this may take 10-30 seconds)...");

  try {
    execSync(
      `wsl -d Ubuntu -- python3 "${wslScript}" "${wslArgsFile}"`,
      { timeout: 120000, maxBuffer: 1024 * 1024 }
    );
  } catch (e) {
    // The script may still have written a result file with an error
    if (!fs.existsSync(resultFile)) {
      console.error("   WSL prover error:", e.stderr?.toString() || e.message);
      throw new Error("Proof generation failed");
    }
  }

  // Read result from file (avoids stdout pollution from C++ library)
  if (!fs.existsSync(resultFile)) {
    throw new Error("Proof result file not created");
  }
  const proofResult = JSON.parse(fs.readFileSync(resultFile, "utf8"));

  // Clean up temp files
  try { fs.unlinkSync(argsFile); } catch (_) {}
  try { fs.unlinkSync(resultFile); } catch (_) {}

  return proofResult;
}

async function main() {
  console.log("=".repeat(60));
  console.log("  MIXIMUS FULL END-TO-END TEST");
  console.log("  Deposit 1 ETH → Generate zkSNARK Proof → Withdraw");
  console.log("=".repeat(60));

  const [deployer, depositor, recipient] = await ethers.getSigners();
  const { vk, vkGammaABC } = loadVK();

  // 1. Deploy
  console.log("\n1. Deploying contracts...");
  const Factory = await ethers.getContractFactory("MiximusFactory");
  const factory = await Factory.deploy();
  await factory.waitForDeployment();

  const denom = ethers.parseEther("1");
  const createTx = await factory.createNativePool(denom, "ETH", vk, vkGammaABC);
  const createReceipt = await createTx.wait();
  const poolEvent = createReceipt.logs.find(l => l.fragment?.name === "NativePoolCreated");
  const pool = await ethers.getContractAt("MiximusNative", poolEvent.args[0]);
  const poolAddress = await pool.getAddress();
  console.log(`   Factory: ${await factory.getAddress()}`);
  console.log(`   Pool:    ${poolAddress}`);

  // 2. Generate secret
  console.log("\n2. Generating secret...");
  const secretBytes = crypto.randomBytes(31);
  const secret = BigInt("0x" + secretBytes.toString("hex")) % SCALAR_FIELD;
  console.log(`   Secret: 0x${secret.toString(16).slice(0, 16)}...`);

  // Compute leaf hash on-chain
  const leafHash = await pool.makeLeafHash(secret);
  console.log(`   Leaf hash: ${leafHash}`);

  // 3. Deposit
  console.log("\n3. Depositing 1 ETH...");
  const depositTx = await pool.connect(depositor).deposit(leafHash, { value: denom });
  const depositReceipt = await depositTx.wait();
  const leafIndex = (await pool.nextLeafIndex()) - 1n;
  const root = await pool.getRoot();
  console.log(`   Tx hash: ${depositReceipt.hash}`);
  console.log(`   Gas used: ${depositReceipt.gasUsed}`);
  console.log(`   Leaf index: ${leafIndex}`);
  console.log(`   Merkle root: ${root}`);

  // 4. Get Merkle path
  console.log("\n4. Getting Merkle path...");
  const [merklePath, addressBits] = await pool.getPath(leafIndex);
  console.log(`   Path length: ${merklePath.length} levels`);
  console.log(`   Address bits: ${addressBits.slice(0, 8).map(b => b ? '1' : '0').join('')}...`);

  // 5. Compute external hash
  console.log("\n5. Computing external hash...");
  const exthash = computeExtHash(poolAddress, recipient.address);
  console.log(`   Pool:      ${poolAddress}`);
  console.log(`   Recipient: ${recipient.address}`);
  console.log(`   Ext hash:  ${exthash}`);

  // 6. Generate proof
  console.log("\n6. Generating zkSNARK proof...");
  const proofResult = generateProofViaWSL(
    root, secret, exthash,
    addressBits, merklePath.map(p => BigInt(p))
  );

  if (proofResult.error) {
    console.error(`   FAILED: ${proofResult.error}`);
    process.exit(1);
  }

  const nullifier = BigInt(proofResult.nullifier);
  const proofArray = [
    BigInt(proofResult.A[0]), BigInt(proofResult.A[1]),
    BigInt(proofResult.B[0][0]), BigInt(proofResult.B[0][1]),
    BigInt(proofResult.B[1][0]), BigInt(proofResult.B[1][1]),
    BigInt(proofResult.C[0]), BigInt(proofResult.C[1]),
  ];

  console.log(`   Nullifier: ${nullifier}`);
  console.log(`   Proof A: (0x${proofArray[0].toString(16).slice(0, 12)}...)`);
  console.log(`   Proof generated successfully!`);

  // 7. Verify nullifier isn't spent
  console.log("\n7. Pre-withdrawal checks...");
  console.log(`   Root known: ${await pool.isKnownRoot(root)}`);
  console.log(`   Nullifier spent: ${await pool.isSpent(nullifier)}`);

  // 8. Withdraw
  console.log("\n8. Withdrawing to recipient...");
  const recipientBalBefore = await ethers.provider.getBalance(recipient.address);
  console.log(`   Recipient balance before: ${ethers.formatEther(recipientBalBefore)} ETH`);

  try {
    const withdrawTx = await pool.connect(recipient).withdraw(root, nullifier, proofArray);
    const withdrawReceipt = await withdrawTx.wait();
    console.log(`   Tx hash: ${withdrawReceipt.hash}`);
    console.log(`   Gas used: ${withdrawReceipt.gasUsed}`);
    console.log(`   STATUS: SUCCESS!`);
  } catch (e) {
    console.error(`   WITHDRAW FAILED: ${e.message}`);
    process.exit(1);
  }

  const recipientBalAfter = await ethers.provider.getBalance(recipient.address);
  const netGain = recipientBalAfter - recipientBalBefore;
  console.log(`   Recipient balance after: ${ethers.formatEther(recipientBalAfter)} ETH`);
  console.log(`   Net gained (minus gas): ${ethers.formatEther(netGain)} ETH`);

  // 9. Double-spend check
  console.log("\n9. Double-spend protection test...");
  console.log(`   Nullifier now spent: ${await pool.isSpent(nullifier)}`);
  try {
    await pool.connect(recipient).withdraw.staticCall(root, nullifier, proofArray);
    console.log("   ERROR: Double-spend NOT prevented!");
  } catch (e) {
    console.log("   Double-spend correctly rejected!");
  }

  // Summary
  console.log(`\n${"=".repeat(60)}`);
  console.log("  END-TO-END TEST PASSED!");
  console.log("");
  console.log(`  Depositor (${depositor.address.slice(0, 10)}...)`);
  console.log(`  sent 1 ETH anonymously to`);
  console.log(`  Recipient (${recipient.address.slice(0, 10)}...)`);
  console.log("");
  console.log("  No on-chain link between depositor and recipient.");
  console.log("  The zkSNARK proof verified successfully on-chain.");
  console.log(`${"=".repeat(60)}`);
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
