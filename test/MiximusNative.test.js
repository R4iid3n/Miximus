/**
 * End-to-end tests for MiximusNative contract
 *
 * Tests deposit, Merkle tree operations, and contract state management.
 * Full zkSNARK proof verification requires the C++ prover (tested separately).
 */

const { expect } = require("chai");
const { ethers } = require("hardhat");
const fs = require("fs");
const path = require("path");

// Load verifying key
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

describe("MiximusNative", function () {
  let factory, nativePool;
  let deployer, user1, user2;
  const DENOMINATION = ethers.parseEther("1"); // 1 ETH

  before(async function () {
    [deployer, user1, user2] = await ethers.getSigners();
    const { vk, vkGammaABC } = loadVK();

    // Deploy Factory
    const Factory = await ethers.getContractFactory("MiximusFactory");
    factory = await Factory.deploy();
    await factory.waitForDeployment();

    // Create native ETH pool via factory
    const tx = await factory.createNativePool(DENOMINATION, "ETH", vk, vkGammaABC);
    const receipt = await tx.wait();
    const event = receipt.logs.find(
      (l) => l.fragment && l.fragment.name === "NativePoolCreated"
    );
    const poolAddr = event.args[0];

    // Attach to the deployed pool
    nativePool = await ethers.getContractAt("MiximusNative", poolAddr);
    console.log(`   Factory: ${await factory.getAddress()}`);
    console.log(`   ETH Pool: ${poolAddr}`);
  });

  describe("Deployment", function () {
    it("should have correct denomination", async function () {
      expect(await nativePool.denomination()).to.equal(DENOMINATION);
    });

    it("should have correct asset symbol", async function () {
      expect(await nativePool.assetSymbol()).to.equal("ETH");
    });

    it("should have initial leaf index of 0", async function () {
      expect(await nativePool.nextLeafIndex()).to.equal(0);
    });

    it("should have an initial valid root", async function () {
      const root = await nativePool.getRoot();
      expect(root).to.not.equal(0);
      expect(await nativePool.isKnownRoot(root)).to.be.true;
    });
  });

  describe("MiMC Hash", function () {
    it("should compute MiMC hash deterministically", async function () {
      const hash1 = await nativePool.mimcHash([42n]);
      const hash2 = await nativePool.mimcHash([42n]);
      expect(hash1).to.equal(hash2);
    });

    it("should produce different hashes for different inputs", async function () {
      const hash1 = await nativePool.mimcHash([1n]);
      const hash2 = await nativePool.mimcHash([2n]);
      expect(hash1).to.not.equal(hash2);
    });

    it("should compute leaf hash correctly", async function () {
      const secret = 123456789n;
      const leaf = await nativePool.makeLeafHash(secret);
      expect(leaf).to.be.gt(0);
    });
  });

  describe("Deposit", function () {
    it("should reject deposit with wrong amount", async function () {
      const fakeLeaf = 12345n;
      await expect(
        nativePool.connect(user1).deposit(fakeLeaf, { value: ethers.parseEther("0.5") })
      ).to.be.revertedWith("Must deposit exact denomination");
    });

    it("should reject deposit with zero value", async function () {
      const fakeLeaf = 12345n;
      await expect(
        nativePool.connect(user1).deposit(fakeLeaf, { value: 0 })
      ).to.be.revertedWith("Must deposit exact denomination");
    });

    it("should accept deposit with exact denomination", async function () {
      // Generate a secret and compute leaf hash
      const secret = BigInt("0x" + Buffer.from(ethers.randomBytes(31)).toString("hex"));
      const leaf = await nativePool.makeLeafHash(secret);

      const initialBalance = await ethers.provider.getBalance(await nativePool.getAddress());
      const initialIndex = await nativePool.nextLeafIndex();
      const initialRoot = await nativePool.getRoot();

      // Deposit 1 ETH
      const tx = await nativePool.connect(user1).deposit(leaf, {
        value: DENOMINATION,
      });
      const receipt = await tx.wait();

      // Verify state changes
      expect(await nativePool.nextLeafIndex()).to.equal(initialIndex + 1n);
      const newRoot = await nativePool.getRoot();
      expect(newRoot).to.not.equal(initialRoot);
      expect(await nativePool.isKnownRoot(newRoot)).to.be.true;
      // Old root should still be valid
      expect(await nativePool.isKnownRoot(initialRoot)).to.be.true;

      // Verify contract received the ETH
      const newBalance = await ethers.provider.getBalance(await nativePool.getAddress());
      expect(newBalance).to.equal(initialBalance + DENOMINATION);

      // Verify deposit event
      const depositEvent = receipt.logs.find(
        (l) => l.fragment && l.fragment.name === "Deposit"
      );
      expect(depositEvent).to.not.be.undefined;
      expect(depositEvent.args[0]).to.equal(leaf); // leafHash
      expect(depositEvent.args[1]).to.equal(initialIndex); // leafIndex

      console.log(`   Deposited 1 ETH, leaf index: ${initialIndex}, new root: ${newRoot}`);
    });

    it("should handle multiple deposits", async function () {
      const startIndex = await nativePool.nextLeafIndex();

      // Make 3 more deposits
      for (let i = 0; i < 3; i++) {
        const secret = BigInt("0x" + Buffer.from(ethers.randomBytes(31)).toString("hex"));
        const leaf = await nativePool.makeLeafHash(secret);
        await nativePool.connect(user1).deposit(leaf, { value: DENOMINATION });
      }

      expect(await nativePool.nextLeafIndex()).to.equal(startIndex + 3n);

      // Contract should hold 4 ETH total (1 from previous test + 3 new)
      const balance = await ethers.provider.getBalance(await nativePool.getAddress());
      expect(balance).to.equal(DENOMINATION * 4n);

      console.log(`   4 deposits complete, contract balance: ${ethers.formatEther(balance)} ETH`);
    });
  });

  describe("Merkle Tree", function () {
    it("should return valid path for deposited leaf", async function () {
      const [path, addressBits] = await nativePool.getPath(0);
      expect(path.length).to.equal(29); // TREE_DEPTH
      expect(addressBits.length).to.equal(29);
    });

    it("should reject path request for non-existent leaf", async function () {
      const nextIndex = await nativePool.nextLeafIndex();
      await expect(nativePool.getPath(nextIndex)).to.be.revertedWith(
        "Leaf not yet inserted"
      );
    });
  });

  describe("Factory", function () {
    it("should track the pool in registry", async function () {
      const poolAddr = await factory.getNativePool(DENOMINATION);
      expect(poolAddr).to.equal(await nativePool.getAddress());
    });

    it("should report correct total pools", async function () {
      expect(await factory.totalPools()).to.equal(1);
    });

    it("should prevent duplicate pool creation", async function () {
      const { vk, vkGammaABC } = loadVK();
      await expect(
        factory.createNativePool(DENOMINATION, "ETH", vk, vkGammaABC)
      ).to.be.revertedWith("Pool already exists");
    });

    it("should allow pool with different denomination", async function () {
      const { vk, vkGammaABC } = loadVK();
      const smallDenom = ethers.parseEther("0.1");
      await factory.createNativePool(smallDenom, "ETH", vk, vkGammaABC);
      expect(await factory.totalPools()).to.equal(2);
      const pool2 = await factory.getNativePool(smallDenom);
      expect(pool2).to.not.equal(ethers.ZeroAddress);
    });
  });

  describe("Nullifier Tracking", function () {
    it("should report no spent nullifiers initially", async function () {
      expect(await nativePool.isSpent(12345n)).to.be.false;
      expect(await nativePool.isSpent(0n)).to.be.false;
    });
  });

  describe("Summary", function () {
    it("should print contract state summary", async function () {
      console.log("\n   === Contract State Summary ===");
      console.log(`   Pool address: ${await nativePool.getAddress()}`);
      console.log(`   Denomination: ${ethers.formatEther(await nativePool.denomination())} ETH`);
      console.log(`   Deposits: ${await nativePool.nextLeafIndex()}`);
      console.log(`   Current root: ${await nativePool.getRoot()}`);
      console.log(`   Contract balance: ${ethers.formatEther(await ethers.provider.getBalance(await nativePool.getAddress()))} ETH`);
      console.log(`   Factory pools: ${await factory.totalPools()}`);
      console.log("   ==============================\n");
    });
  });
});
