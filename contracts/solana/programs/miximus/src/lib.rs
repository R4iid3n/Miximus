/**
 * Miximus Solana Program
 *
 * zkSNARK-based mixer for SOL (native) on Solana.
 * Uses Solana's native alt_bn128 syscalls for Groth16 verification.
 *
 * Architecture:
 *   - MixerPool account: stores denomination, VK, root history, bump
 *   - TreeNodes account: stores full Merkle tree nodes at every level
 *   - NullifierAccount: PDA per nullifier to prevent double-spend
 *   - Deposit: transfers SOL into pool PDA, inserts leaf into Merkle tree
 *   - Withdraw: verifies Groth16 proof via alt_bn128 syscalls, transfers SOL out
 *
 * The zkSNARK circuit is identical to the EVM version (same C++ prover).
 */

use anchor_lang::prelude::*;
use anchor_lang::solana_program::{
    alt_bn128::prelude::{alt_bn128_addition, alt_bn128_multiplication, alt_bn128_pairing},
    keccak,
    program::invoke,
    system_instruction,
};
use std::collections::BTreeMap;

declare_id!("MXMSxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx");

pub const TREE_DEPTH: usize = 29;
pub const MAX_LEAVES: u64 = 1 << TREE_DEPTH;

/// Maximum number of historical roots to keep
pub const ROOT_HISTORY_SIZE: usize = 100;

// =========================================================================
//                      U256 — 256-BIT FIELD ARITHMETIC
// =========================================================================

/// A 256-bit unsigned integer stored as four 64-bit limbs in little-endian order.
/// limbs[0] is the least significant.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct U256 {
    pub limbs: [u64; 4],
}

/// BN254 scalar field modulus:
/// 21888242871839275222246405745257275088548364400416034343698204186575808495617
/// = 0x30644e72e131a029b85045b68181585d2833e84879b9709143e1f593f0000001
const SCALAR_FIELD: U256 = U256 {
    limbs: [
        0x43e1f593f0000001,
        0x2833e84879b97091,
        0xb85045b68181585d,
        0x30644e72e131a029,
    ],
};

/// BN254 base field modulus (for G1 point negation):
/// 21888242871839275222246405745257275088696311157297823662689037894645226208583
/// = 0x30644e72e131a029b85045b68181585d97816a916871ca8d3c208c16d87cfd47
const BASE_FIELD: U256 = U256 {
    limbs: [
        0x3c208c16d87cfd47,
        0x97816a916871ca8d,
        0xb85045b68181585d,
        0x30644e72e131a029,
    ],
};

impl U256 {
    pub const ZERO: U256 = U256 { limbs: [0, 0, 0, 0] };
    pub const ONE: U256 = U256 { limbs: [1, 0, 0, 0] };

    /// Create a U256 from a big-endian byte array [u8; 32].
    pub fn from_be_bytes(bytes: &[u8; 32]) -> Self {
        let mut limbs = [0u64; 4];
        // bytes[0..8] -> most significant limb (limbs[3])
        for i in 0..4 {
            let offset = (3 - i) * 8;
            limbs[i] = u64::from_be_bytes([
                bytes[offset],
                bytes[offset + 1],
                bytes[offset + 2],
                bytes[offset + 3],
                bytes[offset + 4],
                bytes[offset + 5],
                bytes[offset + 6],
                bytes[offset + 7],
            ]);
        }
        U256 { limbs }
    }

    /// Convert to a big-endian byte array [u8; 32].
    pub fn to_be_bytes(&self) -> [u8; 32] {
        let mut bytes = [0u8; 32];
        for i in 0..4 {
            let offset = (3 - i) * 8;
            let b = self.limbs[i].to_be_bytes();
            bytes[offset..offset + 8].copy_from_slice(&b);
        }
        bytes
    }

    /// Convert to a little-endian byte array [u8; 32] (for alt_bn128 syscalls).
    pub fn to_le_bytes(&self) -> [u8; 32] {
        let mut bytes = [0u8; 32];
        for i in 0..4 {
            let offset = i * 8;
            let b = self.limbs[i].to_le_bytes();
            bytes[offset..offset + 8].copy_from_slice(&b);
        }
        bytes
    }

    /// Create a U256 from a little-endian byte array [u8; 32].
    pub fn from_le_bytes(bytes: &[u8; 32]) -> Self {
        let mut limbs = [0u64; 4];
        for i in 0..4 {
            let offset = i * 8;
            limbs[i] = u64::from_le_bytes([
                bytes[offset],
                bytes[offset + 1],
                bytes[offset + 2],
                bytes[offset + 3],
                bytes[offset + 4],
                bytes[offset + 5],
                bytes[offset + 6],
                bytes[offset + 7],
            ]);
        }
        U256 { limbs }
    }

    /// Returns true if self >= other.
    pub fn gte(&self, other: &U256) -> bool {
        for i in (0..4).rev() {
            if self.limbs[i] > other.limbs[i] {
                return true;
            }
            if self.limbs[i] < other.limbs[i] {
                return false;
            }
        }
        true // equal
    }

    /// Returns true if self == 0.
    pub fn is_zero(&self) -> bool {
        self.limbs[0] == 0 && self.limbs[1] == 0 && self.limbs[2] == 0 && self.limbs[3] == 0
    }

    /// Wrapping addition: self + other, returns (result, carry).
    fn add_with_carry(&self, other: &U256) -> (U256, bool) {
        let mut result = [0u64; 4];
        let mut carry: u64 = 0;
        for i in 0..4 {
            let (s1, c1) = self.limbs[i].overflowing_add(other.limbs[i]);
            let (s2, c2) = s1.overflowing_add(carry);
            result[i] = s2;
            carry = (c1 as u64) + (c2 as u64);
        }
        (U256 { limbs: result }, carry > 0)
    }

    /// Wrapping subtraction: self - other, returns (result, borrow).
    fn sub_with_borrow(&self, other: &U256) -> (U256, bool) {
        let mut result = [0u64; 4];
        let mut borrow: u64 = 0;
        for i in 0..4 {
            let (s1, b1) = self.limbs[i].overflowing_sub(other.limbs[i]);
            let (s2, b2) = s1.overflowing_sub(borrow);
            result[i] = s2;
            borrow = (b1 as u64) + (b2 as u64);
        }
        (U256 { limbs: result }, borrow > 0)
    }

    /// Modular addition: (self + other) % modulus.
    pub fn addmod(&self, other: &U256, modulus: &U256) -> U256 {
        let a = self.reduce(modulus);
        let b = other.reduce(modulus);
        let (sum, carry) = a.add_with_carry(&b);
        if carry || sum.gte(modulus) {
            let (result, _) = sum.sub_with_borrow(modulus);
            result
        } else {
            sum
        }
    }

    /// Reduce self modulo modulus. This uses repeated subtraction for values
    /// that are at most 2x the modulus (which is the case after single add).
    /// For general reduction we use a proper method.
    pub fn reduce(&self, modulus: &U256) -> U256 {
        if self.gte(modulus) {
            // For values that could be much larger (e.g. from multiplication),
            // we need Barrett or similar. For values from addmod (at most 2*mod),
            // single subtraction suffices.
            let (result, _) = self.sub_with_borrow(modulus);
            if result.gte(modulus) {
                // Shouldn't happen for addmod, but as safety for reduce on
                // values close to modulus
                return result.reduce(modulus);
            }
            result
        } else {
            *self
        }
    }

    /// Full 256x256 -> 512-bit multiplication, returning low and high U256.
    fn mul_wide(&self, other: &U256) -> (U256, U256) {
        // Schoolbook multiplication using 64-bit limbs
        let mut result = [0u128; 8];
        for i in 0..4 {
            let mut carry: u128 = 0;
            for j in 0..4 {
                let prod = (self.limbs[i] as u128) * (other.limbs[j] as u128)
                    + result[i + j]
                    + carry;
                result[i + j] = prod & 0xFFFFFFFFFFFFFFFF;
                carry = prod >> 64;
            }
            result[i + 4] += carry;
        }
        let lo = U256 {
            limbs: [
                result[0] as u64,
                result[1] as u64,
                result[2] as u64,
                result[3] as u64,
            ],
        };
        let hi = U256 {
            limbs: [
                result[4] as u64,
                result[5] as u64,
                result[6] as u64,
                result[7] as u64,
            ],
        };
        (lo, hi)
    }

    /// Modular multiplication: (self * other) % modulus.
    /// Uses a simple shift-and-subtract algorithm for the 512-bit intermediate.
    pub fn mulmod(&self, other: &U256, modulus: &U256) -> U256 {
        if modulus.is_zero() {
            return U256::ZERO;
        }
        let a = self.reduce(modulus);
        let b = other.reduce(modulus);

        // Use 512-bit intermediate and reduce
        let (lo, hi) = a.mul_wide(&b);
        u512_mod(&lo, &hi, modulus)
    }

    /// Modular subtraction: (self - other) % modulus, assuming both < modulus.
    pub fn submod(&self, other: &U256, modulus: &U256) -> U256 {
        let a = self.reduce(modulus);
        let b = other.reduce(modulus);
        if a.gte(&b) {
            let (result, _) = a.sub_with_borrow(&b);
            result
        } else {
            // a < b, so result = modulus - (b - a)
            let (diff, _) = b.sub_with_borrow(&a);
            let (result, _) = modulus.sub_with_borrow(&diff);
            result
        }
    }
}

/// Reduce a 512-bit number (lo + hi * 2^256) modulo m.
/// Uses a shift-and-subtract approach.
fn u512_mod(lo: &U256, hi: &U256, m: &U256) -> U256 {
    if hi.is_zero() {
        return lo.reduce(m);
    }

    // We need to compute (hi * 2^256 + lo) mod m.
    // Approach: reduce hi mod m first, then iteratively compute hi_reduced * 2^256 mod m
    // by doing hi_reduced * (2^256 mod m) via repeated squaring.
    //
    // But a simpler approach: process bit by bit from the top of hi.

    // Start with result = 0, process each bit of the 512-bit number from MSB to LSB.
    // For each bit: result = (result * 2 + bit) mod m

    let mut result = U256::ZERO;

    // Process hi bits (bits 511 down to 256)
    for i in (0..256).rev() {
        let limb_idx = i / 64;
        let bit_idx = i % 64;
        let bit = (hi.limbs[limb_idx] >> bit_idx) & 1;

        // result = result * 2 mod m
        result = result.addmod(&result, m);

        // result = result + bit mod m
        if bit == 1 {
            result = result.addmod(&U256::ONE, m);
        }
    }

    // Process lo bits (bits 255 down to 0)
    for i in (0..256).rev() {
        let limb_idx = i / 64;
        let bit_idx = i % 64;
        let bit = (lo.limbs[limb_idx] >> bit_idx) & 1;

        result = result.addmod(&result, m);

        if bit == 1 {
            result = result.addmod(&U256::ONE, m);
        }
    }

    result
}

/// Parse a decimal string into a U256 at compile time is not feasible,
/// so we provide a runtime parser.
fn u256_from_decimal(s: &str) -> U256 {
    let mut result = U256::ZERO;
    let ten = U256 { limbs: [10, 0, 0, 0] };
    for ch in s.bytes() {
        let digit = (ch - b'0') as u64;
        // result = result * 10 + digit
        let (r10_lo, r10_hi) = result.mul_wide(&ten);
        result = u512_mod(&r10_lo, &r10_hi, &U256 { limbs: [u64::MAX; 4] });
        // For decimal parsing we won't overflow a U256 for our constants,
        // so hi should be zero. But let's be safe:
        if !r10_hi.is_zero() {
            // This shouldn't happen for our 77-digit field elements
            result = u512_mod(&r10_lo, &r10_hi, &SCALAR_FIELD);
        }
        let d = U256 { limbs: [digit, 0, 0, 0] };
        let (sum, _carry) = result.add_with_carry(&d);
        result = sum;
    }
    result
}

// =========================================================================
//                         MiMC CIPHER & HASH
// =========================================================================

/// MiMC-p/p cipher with exponent 7, 91 rounds.
/// Round constants are generated from keccak256 hash chain starting with keccak256("mimc").
/// All arithmetic in BN254 scalar field.
///
///   for i in 0..91:
///     c = keccak256(c)       // c starts as keccak256("mimc")
///     t = x + c_i + k        // all mod SCALAR_FIELD
///     x = t^7                // mod SCALAR_FIELD
///   return x + k
fn mimc_cipher(in_x: &U256, in_k: &U256) -> U256 {
    let q = &SCALAR_FIELD;

    // Seed: keccak256("mimc")
    let seed_hash = keccak::hash(b"mimc");
    let mut c_bytes: [u8; 32] = seed_hash.to_bytes();

    let mut x = in_x.reduce(q);
    let k = in_k.reduce(q);

    for _ in 0..91 {
        // c = keccak256(c) — next round constant
        let h = keccak::hash(&c_bytes);
        c_bytes = h.to_bytes();

        // Parse round constant as big-endian U256, reduce mod q
        let c = U256::from_be_bytes(&c_bytes).reduce(q);

        // t = x + c + k  (mod q)
        let t = x.addmod(&c, q).addmod(&k, q);

        // t^7 = t * (t^2)^3
        let t2 = t.mulmod(&t, q);              // t^2
        let t4 = t2.mulmod(&t2, q);            // t^4
        let t6 = t4.mulmod(&t2, q);            // t^6
        x = t6.mulmod(&t, q);                  // t^7
    }

    // Final key addition
    x.addmod(&k, q)
}

/// MiMC hash with custom IV using Miyaguchi-Preneel compression.
/// For each element x in data:
///   h = cipher(x, r)
///   r = r + x + h   (all mod SCALAR_FIELD)
fn mimc_hash_with_iv(data: &[U256], iv: &U256) -> U256 {
    let q = &SCALAR_FIELD;
    let mut r = iv.reduce(q);

    for x in data {
        let x_reduced = x.reduce(q);
        let h = mimc_cipher(&x_reduced, &r);
        // r = r + x + h  (Miyaguchi-Preneel)
        r = r.addmod(&x_reduced, q).addmod(&h, q);
    }
    r
}

/// MiMC hash with IV=0 (standard hash).
fn mimc_hash(data: &[U256]) -> U256 {
    mimc_hash_with_iv(data, &U256::ZERO)
}

// =========================================================================
//                         LEVEL IVs (29 levels)
// =========================================================================

/// Returns the level-specific IV for Merkle tree hashing.
/// These match the ethsnarks C++ circuit exactly.
fn level_iv(level: usize) -> U256 {
    // We store the IVs as hex (derived from the decimal constants).
    // The decimal values are converted to U256 at runtime.
    match level {
        0 => u256_from_decimal("149674538925118052205057075966660054952481571156186698930522557832224430770"),
        1 => u256_from_decimal("9670701465464311903249220692483401938888498641874948577387207195814981706974"),
        2 => u256_from_decimal("18318710344500308168304415114839554107298291987930233567781901093928276468271"),
        3 => u256_from_decimal("6597209388525824933845812104623007130464197923269180086306970975123437805179"),
        4 => u256_from_decimal("21720956803147356712695575768577036859892220417043839172295094119877855004262"),
        5 => u256_from_decimal("10330261616520855230513677034606076056972336573153777401182178891807369896722"),
        6 => u256_from_decimal("17466547730316258748333298168566143799241073466140136663575045164199607937939"),
        7 => u256_from_decimal("18881017304615283094648494495339883533502299318365959655029893746755475886610"),
        8 => u256_from_decimal("21580915712563378725413940003372103925756594604076607277692074507345076595494"),
        9 => u256_from_decimal("12316305934357579015754723412431647910012873427291630993042374701002287130550"),
        10 => u256_from_decimal("18905410889238873726515380969411495891004493295170115920825550288019118582494"),
        11 => u256_from_decimal("12819107342879320352602391015489840916114959026915005817918724958237245903353"),
        12 => u256_from_decimal("8245796392944118634696709403074300923517437202166861682117022548371601758802"),
        13 => u256_from_decimal("16953062784314687781686527153155644849196472783922227794465158787843281909585"),
        14 => u256_from_decimal("19346880451250915556764413197424554385509847473349107460608536657852472800734"),
        15 => u256_from_decimal("14486794857958402714787584825989957493343996287314210390323617462452254101347"),
        16 => u256_from_decimal("11127491343750635061768291849689189917973916562037173191089384809465548650641"),
        17 => u256_from_decimal("12217916643258751952878742936579902345100885664187835381214622522318889050675"),
        18 => u256_from_decimal("722025110834410790007814375535296040832778338853544117497481480537806506496"),
        19 => u256_from_decimal("15115624438829798766134408951193645901537753720219896384705782209102859383951"),
        20 => u256_from_decimal("11495230981884427516908372448237146604382590904456048258839160861769955046544"),
        21 => u256_from_decimal("16867999085723044773810250829569850875786210932876177117428755424200948460050"),
        22 => u256_from_decimal("1884116508014449609846749684134533293456072152192763829918284704109129550542"),
        23 => u256_from_decimal("14643335163846663204197941112945447472862168442334003800621296569318670799451"),
        24 => u256_from_decimal("1933387276732345916104540506251808516402995586485132246682941535467305930334"),
        25 => u256_from_decimal("7286414555941977227951257572976885370489143210539802284740420664558593616067"),
        26 => u256_from_decimal("16932161189449419608528042274282099409408565503929504242784173714823499212410"),
        27 => u256_from_decimal("16562533130736679030886586765487416082772837813468081467237161865787494093536"),
        28 => u256_from_decimal("6037428193077828806710267464232314380014232668931818917272972397574634037180"),
        _ => panic!("Invalid Merkle tree level"),
    }
}

// =========================================================================
//                         MERKLE TREE HELPERS
// =========================================================================

/// Compute the zero hash at a given level. Level 0 zero = 0.
/// zero[level+1] = mimcHashWithIV([zero[level], zero[level]], levelIV[level])
fn compute_zero_hashes() -> [U256; TREE_DEPTH + 1] {
    let mut zeros = [U256::ZERO; TREE_DEPTH + 1];
    // zeros[0] = 0 (leaf zero value)
    for i in 0..TREE_DEPTH {
        let iv = level_iv(i);
        zeros[i + 1] = mimc_hash_with_iv(&[zeros[i], zeros[i]], &iv);
    }
    zeros
}

/// Compute the initial root (all-zero tree).
fn compute_initial_root() -> [u8; 32] {
    let zeros = compute_zero_hashes();
    zeros[TREE_DEPTH].to_be_bytes()
}

/// Get a node from the tree_nodes map, or return the zero hash for that level.
fn get_node(tree_nodes: &BTreeMap<u64, [u8; 32]>, level: usize, index: usize, zero_hashes: &[U256; TREE_DEPTH + 1]) -> U256 {
    let key = encode_tree_key(level, index);
    match tree_nodes.get(&key) {
        Some(bytes) => U256::from_be_bytes(bytes),
        None => zero_hashes[level],
    }
}

/// Encode a (level, index) pair into a single u64 key for the BTreeMap.
/// We use the upper 5 bits for level (0..29) and the lower 59 bits for index.
/// This supports up to 2^29 leaves (index up to 2^29 - 1 at level 0,
/// and index up to 0 at level 29).
fn encode_tree_key(level: usize, index: usize) -> u64 {
    ((level as u64) << 59) | (index as u64)
}

/// Insert a leaf into the Merkle tree and return the new root.
/// Stores all intermediate nodes so getPath works.
fn insert_leaf_into_tree(
    tree_nodes: &mut BTreeMap<u64, [u8; 32]>,
    leaf: &[u8; 32],
    leaf_index: usize,
) -> [u8; 32] {
    let zero_hashes = compute_zero_hashes();

    // Store leaf at level 0
    let key0 = encode_tree_key(0, leaf_index);
    tree_nodes.insert(key0, *leaf);

    let mut current = U256::from_be_bytes(leaf);
    let mut idx = leaf_index;

    for level in 0..TREE_DEPTH {
        let parent_idx = idx / 2;
        let iv = level_iv(level);

        let (left, right) = if idx % 2 == 0 {
            // Current node is left child; sibling is right (idx+1)
            let right = get_node(tree_nodes, level, idx + 1, &zero_hashes);
            (current, right)
        } else {
            // Current node is right child; sibling is left (idx-1)
            let left = get_node(tree_nodes, level, idx - 1, &zero_hashes);
            (left, current)
        };

        current = mimc_hash_with_iv(&[left, right], &iv);

        // Store this intermediate node at level+1
        let key = encode_tree_key(level + 1, parent_idx);
        tree_nodes.insert(key, current.to_be_bytes());

        idx = parent_idx;
    }

    current.to_be_bytes()
}

/// Retrieve the Merkle authentication path for a given leaf index.
/// Returns (path, address_bits) where path[i] is the sibling at level i
/// and address_bits[i] indicates whether the node is a right child.
fn get_merkle_path(
    tree_nodes: &BTreeMap<u64, [u8; 32]>,
    leaf_index: usize,
) -> ([U256; TREE_DEPTH], [bool; TREE_DEPTH]) {
    let zero_hashes = compute_zero_hashes();
    let mut path = [U256::ZERO; TREE_DEPTH];
    let mut address_bits = [false; TREE_DEPTH];

    for i in 0..TREE_DEPTH {
        let node_idx = leaf_index >> i;
        address_bits[i] = (node_idx & 1) == 1;
        let sibling_idx = node_idx ^ 1;
        path[i] = get_node(tree_nodes, i, sibling_idx, &zero_hashes);
    }

    (path, address_bits)
}

// =========================================================================
//                     GROTH16 VERIFICATION (alt_bn128)
// =========================================================================

/// Solana's alt_bn128 syscalls use LITTLE-ENDIAN encoding for field elements
/// (each coordinate is 32 bytes LE). G1 points are 64 bytes (x, y).
/// G2 points are 128 bytes (x_c0, x_c1, y_c0, y_c1) — but see note below.
///
/// IMPORTANT: The EVM precompiles use BIG-ENDIAN and G2 ordering [[X.c1,X.c0],[Y.c1,Y.c0]].
/// Solana's alt_bn128 syscalls also follow this convention for the serialized format.
/// We store VK values as big-endian [u8;32] and convert to the syscall format as needed.

/// Negate a G1 point on BN254. A G1 point (x, y) is negated as (x, -y mod p).
/// Input/output: 64 bytes in alt_bn128 format (LE coordinates).
fn negate_g1(point: &[u8; 64]) -> [u8; 64] {
    let mut result = [0u8; 64];
    // x stays the same
    result[0..32].copy_from_slice(&point[0..32]);

    // y = p - y (in little-endian)
    let y = U256::from_le_bytes(&point[32..64].try_into().unwrap());
    if y.is_zero() {
        result[32..64].copy_from_slice(&[0u8; 32]);
    } else {
        let neg_y = BASE_FIELD.submod(&y, &BASE_FIELD);
        result[32..64].copy_from_slice(&neg_y.to_le_bytes());
    }
    result
}

/// Convert a big-endian U256 ([u8;32] BE) to the alt_bn128 LE format.
fn be_to_le(be_bytes: &[u8; 32]) -> [u8; 32] {
    let mut le = [0u8; 32];
    for i in 0..32 {
        le[i] = be_bytes[31 - i];
    }
    le
}

/// Convert a G1 point from big-endian (x_be, y_be) to alt_bn128 LE format (64 bytes).
fn g1_be_to_le(x_be: &[u8; 32], y_be: &[u8; 32]) -> [u8; 64] {
    let mut out = [0u8; 64];
    out[0..32].copy_from_slice(&be_to_le(x_be));
    out[32..64].copy_from_slice(&be_to_le(y_be));
    out
}

/// Convert a G2 point from big-endian storage to alt_bn128 LE format (128 bytes).
/// VK storage order (from ethsnarks export): [[X.c1, X.c0], [Y.c1, Y.c0]]
/// This maps to 4 big-endian U256 values: x_c1, x_c0, y_c1, y_c0.
///
/// Solana alt_bn128 pairing expects G2 as: x_c0_le, x_c1_le, y_c0_le, y_c1_le
fn g2_be_to_le(x_c1_be: &[u8; 32], x_c0_be: &[u8; 32], y_c1_be: &[u8; 32], y_c0_be: &[u8; 32]) -> [u8; 128] {
    let mut out = [0u8; 128];
    out[0..32].copy_from_slice(&be_to_le(x_c0_be));
    out[32..64].copy_from_slice(&be_to_le(x_c1_be));
    out[64..96].copy_from_slice(&be_to_le(y_c0_be));
    out[96..128].copy_from_slice(&be_to_le(y_c1_be));
    out
}

/// Parse the verifying key from its serialized format.
///
/// VK layout (all values as big-endian [u8; 32]):
///   [0..2]   = alpha G1 (x, y)               — 2 x 32 = 64 bytes
///   [2..6]   = beta G2 (x.c1, x.c0, y.c1, y.c0) — 4 x 32 = 128 bytes
///   [6..10]  = gamma G2                       — 4 x 32 = 128 bytes
///   [10..14] = delta G2                       — 4 x 32 = 128 bytes
///   [14..]   = gammaABC G1 points, each 2 x 32 = 64 bytes
///
/// Total fixed: 14 * 32 = 448 bytes.
/// gammaABC has at least 2 points (for 1 public input): 14*32 + 2*64 = 576 bytes minimum.
struct VerifyingKey {
    alpha_g1: [u8; 64],   // alt_bn128 LE format
    beta_g2: [u8; 128],
    gamma_g2: [u8; 128],
    delta_g2: [u8; 128],
    gamma_abc: Vec<[u8; 64]>,  // alt_bn128 LE format
}

fn parse_vk(vk_data: &[u8]) -> Option<VerifyingKey> {
    if vk_data.len() < 14 * 32 + 2 * 64 {
        return None;
    }

    // Helper to extract a 32-byte chunk
    let chunk = |idx: usize| -> [u8; 32] {
        let mut buf = [0u8; 32];
        buf.copy_from_slice(&vk_data[idx * 32..(idx + 1) * 32]);
        buf
    };

    // alpha G1: vk[0], vk[1]
    let alpha_g1 = g1_be_to_le(&chunk(0), &chunk(1));

    // beta G2: vk[2..6] = x.c1, x.c0, y.c1, y.c0
    let beta_g2 = g2_be_to_le(&chunk(2), &chunk(3), &chunk(4), &chunk(5));

    // gamma G2: vk[6..10]
    let gamma_g2 = g2_be_to_le(&chunk(6), &chunk(7), &chunk(8), &chunk(9));

    // delta G2: vk[10..14]
    let delta_g2 = g2_be_to_le(&chunk(10), &chunk(11), &chunk(12), &chunk(13));

    // gammaABC G1 points: from byte 14*32 onward, each is 2*32=64 bytes (BE)
    let abc_data = &vk_data[14 * 32..];
    if abc_data.len() % 64 != 0 {
        return None;
    }
    let num_abc = abc_data.len() / 64;
    let mut gamma_abc = Vec::with_capacity(num_abc);
    for i in 0..num_abc {
        let offset = i * 64;
        let mut x_be = [0u8; 32];
        let mut y_be = [0u8; 32];
        x_be.copy_from_slice(&abc_data[offset..offset + 32]);
        y_be.copy_from_slice(&abc_data[offset + 32..offset + 64]);
        gamma_abc.push(g1_be_to_le(&x_be, &y_be));
    }

    Some(VerifyingKey {
        alpha_g1,
        beta_g2,
        gamma_g2,
        delta_g2,
        gamma_abc,
    })
}

/// Verify a Groth16 proof using Solana's alt_bn128 syscalls.
///
/// Proof layout (big-endian):
///   A: G1 (x, y)     = 64 bytes
///   B: G2 (x.c1, x.c0, y.c1, y.c0) = 128 bytes
///   C: G1 (x, y)     = 64 bytes
///   Total: 256 bytes
///
/// Public input is computed as: MiMC(root, nullifier, ext_hash) with IV=0
///
/// Verification:
///   1. Compute pub_input = MiMC(root, nullifier, ext_hash)
///   2. Compute vk_x = gammaABC[0] + pub_input * gammaABC[1]
///   3. Check: e(A, B) * e(-alpha, beta) * e(-vk_x, gamma) * e(-C, delta) == 1
fn verify_groth16_proof(
    vk_data: &[u8],
    root: &[u8; 32],
    nullifier: &[u8; 32],
    ext_hash: &[u8; 32],
    proof: &[u8],
) -> bool {
    // Parse proof: must be exactly 256 bytes
    if proof.len() != 256 {
        msg!("Invalid proof length: {}", proof.len());
        return false;
    }

    // Parse verifying key
    let vk = match parse_vk(vk_data) {
        Some(vk) => vk,
        None => {
            msg!("Failed to parse verifying key");
            return false;
        }
    };

    // We need at least 2 gammaABC points for 1 public input
    if vk.gamma_abc.len() < 2 {
        msg!("VK gammaABC too short");
        return false;
    }

    // Compute public input hash: MiMC(root, nullifier, ext_hash)
    let root_u256 = U256::from_be_bytes(root);
    let nullifier_u256 = U256::from_be_bytes(nullifier);
    let ext_hash_u256 = U256::from_be_bytes(ext_hash);

    let pub_input = mimc_hash(&[root_u256, nullifier_u256, ext_hash_u256]);
    let pub_input_le = pub_input.to_le_bytes();

    // Parse proof points from big-endian format
    let mut a_x_be = [0u8; 32];
    let mut a_y_be = [0u8; 32];
    a_x_be.copy_from_slice(&proof[0..32]);
    a_y_be.copy_from_slice(&proof[32..64]);
    let proof_a = g1_be_to_le(&a_x_be, &a_y_be);

    // B is G2: proof[64..192] = x.c1, x.c0, y.c1, y.c0 (big-endian)
    let mut b_xc1_be = [0u8; 32];
    let mut b_xc0_be = [0u8; 32];
    let mut b_yc1_be = [0u8; 32];
    let mut b_yc0_be = [0u8; 32];
    b_xc1_be.copy_from_slice(&proof[64..96]);
    b_xc0_be.copy_from_slice(&proof[96..128]);
    b_yc1_be.copy_from_slice(&proof[128..160]);
    b_yc0_be.copy_from_slice(&proof[160..192]);
    let proof_b = g2_be_to_le(&b_xc1_be, &b_xc0_be, &b_yc1_be, &b_yc0_be);

    let mut c_x_be = [0u8; 32];
    let mut c_y_be = [0u8; 32];
    c_x_be.copy_from_slice(&proof[192..224]);
    c_y_be.copy_from_slice(&proof[224..256]);
    let proof_c = g1_be_to_le(&c_x_be, &c_y_be);

    // Step 2: Compute vk_x = gammaABC[0] + pub_input * gammaABC[1]
    //
    // EC scalar multiplication: pub_input * gammaABC[1]
    // alt_bn128_multiplication input: G1 point (64 bytes) + scalar (32 bytes LE) = 96 bytes
    let mut mul_input = [0u8; 96];
    mul_input[0..64].copy_from_slice(&vk.gamma_abc[1]);
    mul_input[64..96].copy_from_slice(&pub_input_le);

    let mul_result = match alt_bn128_multiplication(&mul_input) {
        Ok(result) => result,
        Err(_) => {
            msg!("alt_bn128_multiplication failed");
            return false;
        }
    };

    // EC point addition: gammaABC[0] + (pub_input * gammaABC[1])
    // alt_bn128_addition input: G1 point (64 bytes) + G1 point (64 bytes) = 128 bytes
    let mut add_input = [0u8; 128];
    add_input[0..64].copy_from_slice(&vk.gamma_abc[0]);
    add_input[64..128].copy_from_slice(&mul_result);

    let vk_x: [u8; 64] = match alt_bn128_addition(&add_input) {
        Ok(result) => {
            let mut arr = [0u8; 64];
            arr.copy_from_slice(&result);
            arr
        }
        Err(_) => {
            msg!("alt_bn128_addition failed");
            return false;
        }
    };

    // Step 3: Pairing check
    // e(A, B) * e(-alpha, beta) * e(-vk_x, gamma) * e(-C, delta) == 1
    //
    // alt_bn128_pairing input: sequence of (G1, G2) pairs, each 192 bytes
    // 4 pairs = 768 bytes
    //
    // Pair 1: (A, B)
    // Pair 2: (-alpha, beta)
    // Pair 3: (-vk_x, gamma)
    // Pair 4: (-C, delta)

    let neg_alpha = negate_g1(&{
        let mut arr = [0u8; 64];
        arr.copy_from_slice(&vk.alpha_g1);
        arr
    });
    let neg_vk_x = negate_g1(&vk_x);
    let neg_c = negate_g1(&{
        let mut arr = [0u8; 64];
        arr.copy_from_slice(&proof_c);
        arr
    });

    let mut pairing_input = [0u8; 768];

    // Pair 1: (A, B)
    pairing_input[0..64].copy_from_slice(&proof_a);
    pairing_input[64..192].copy_from_slice(&proof_b);

    // Pair 2: (-alpha, beta)
    pairing_input[192..256].copy_from_slice(&neg_alpha);
    pairing_input[256..384].copy_from_slice(&vk.beta_g2);

    // Pair 3: (-vk_x, gamma)
    pairing_input[384..448].copy_from_slice(&neg_vk_x);
    pairing_input[448..576].copy_from_slice(&vk.gamma_g2);

    // Pair 4: (-C, delta)
    pairing_input[576..640].copy_from_slice(&neg_c);
    pairing_input[640..768].copy_from_slice(&vk.delta_g2);

    // Call pairing check
    match alt_bn128_pairing(&pairing_input) {
        Ok(result) => {
            // Result is 32 bytes: 1 if pairing check passed, 0 otherwise
            // The result is a single byte that is 1 on success
            if result.len() >= 32 {
                // Check if the last byte is 1 (LE encoding of the result)
                result[31] == 1 && result[0..31].iter().all(|&b| b == 0)
            } else {
                false
            }
        }
        Err(_) => {
            msg!("alt_bn128_pairing failed");
            false
        }
    }
}

/// Compute external hash binding proof to this contract + recipient.
/// ext_hash = sha256(contract_address || recipient_address) mod SCALAR_FIELD
fn compute_ext_hash(contract: &Pubkey, recipient: &Pubkey) -> [u8; 32] {
    let hash = anchor_lang::solana_program::hash::hashv(&[
        contract.as_ref(),
        recipient.as_ref(),
    ]);
    let h = U256::from_be_bytes(&hash.to_bytes());
    let reduced = h.reduce(&SCALAR_FIELD);
    reduced.to_be_bytes()
}

// =========================================================================
//                          ANCHOR PROGRAM
// =========================================================================

#[program]
pub mod miximus {
    use super::*;

    /// Initialize a new mixer pool for SOL.
    ///
    /// `vk_data`: Serialized verifying key (14 uint256 values + gammaABC G1 points),
    ///            all as big-endian [u8; 32] values concatenated.
    pub fn initialize(
        ctx: Context<Initialize>,
        denomination: u64,
        vk_data: Vec<u8>,
    ) -> Result<()> {
        let pool = &mut ctx.accounts.pool;
        let tree_account = &mut ctx.accounts.tree_nodes;

        pool.authority = ctx.accounts.authority.key();
        pool.denomination = denomination;
        pool.next_leaf_index = 0;
        pool.vk_data = vk_data;
        pool.bump = ctx.bumps.pool;

        // Initialize the Merkle tree: compute and store the initial root
        let initial_root = compute_initial_root();
        pool.current_root = initial_root;
        pool.root_history = vec![initial_root];

        // Initialize tree_nodes account
        tree_account.pool = pool.key();
        tree_account.nodes = BTreeMap::new();

        msg!(
            "Miximus pool initialized: denomination={} lamports",
            denomination
        );
        Ok(())
    }

    /// Deposit SOL into the mixer.
    ///
    /// `leaf_hash`: The commitment (leaf) to insert, as big-endian [u8; 32].
    pub fn deposit(ctx: Context<DepositSol>, leaf_hash: [u8; 32]) -> Result<()> {
        let pool = &mut ctx.accounts.pool;
        let tree_account = &mut ctx.accounts.tree_nodes;

        require!(pool.next_leaf_index < MAX_LEAVES, MiximusError::TreeFull);

        // Transfer SOL from depositor to pool PDA
        invoke(
            &system_instruction::transfer(
                &ctx.accounts.depositor.key(),
                &ctx.accounts.pool_vault.key(),
                pool.denomination,
            ),
            &[
                ctx.accounts.depositor.to_account_info(),
                ctx.accounts.pool_vault.to_account_info(),
                ctx.accounts.system_program.to_account_info(),
            ],
        )?;

        let leaf_index = pool.next_leaf_index;
        pool.next_leaf_index += 1;

        // Insert leaf into Merkle tree with full node storage
        let new_root = insert_leaf_into_tree(
            &mut tree_account.nodes,
            &leaf_hash,
            leaf_index as usize,
        );
        pool.current_root = new_root;

        // Add to root history (ring buffer)
        if pool.root_history.len() >= ROOT_HISTORY_SIZE {
            pool.root_history.remove(0);
        }
        pool.root_history.push(new_root);

        emit!(DepositEvent {
            leaf_hash,
            leaf_index,
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }

    /// Batch deposit SOL — deposit N units in a single transaction.
    ///
    /// Due to Solana compute limits (~200K CU per leaf insertion),
    /// practical batch size is limited to ~2-3 per transaction.
    pub fn batch_deposit(ctx: Context<DepositSol>, leaf_hashes: Vec<[u8; 32]>) -> Result<()> {
        let pool = &mut ctx.accounts.pool;
        let tree_account = &mut ctx.accounts.tree_nodes;
        let count = leaf_hashes.len();

        require!(count > 0 && count <= 20, MiximusError::InvalidBatchSize);
        require!(
            pool.next_leaf_index + count as u64 <= MAX_LEAVES,
            MiximusError::TreeFull
        );

        // Transfer total SOL from depositor to pool PDA
        let total_amount = pool.denomination * count as u64;
        invoke(
            &system_instruction::transfer(
                &ctx.accounts.depositor.key(),
                &ctx.accounts.pool_vault.key(),
                total_amount,
            ),
            &[
                ctx.accounts.depositor.to_account_info(),
                ctx.accounts.pool_vault.to_account_info(),
                ctx.accounts.system_program.to_account_info(),
            ],
        )?;

        for leaf_hash in leaf_hashes.iter() {
            let leaf_index = pool.next_leaf_index;
            pool.next_leaf_index += 1;

            let new_root = insert_leaf_into_tree(
                &mut tree_account.nodes,
                leaf_hash,
                leaf_index as usize,
            );
            pool.current_root = new_root;

            if pool.root_history.len() >= ROOT_HISTORY_SIZE {
                pool.root_history.remove(0);
            }
            pool.root_history.push(new_root);

            emit!(DepositEvent {
                leaf_hash: *leaf_hash,
                leaf_index,
                timestamp: Clock::get()?.unix_timestamp,
            });
        }

        Ok(())
    }

    /// Withdraw SOL from the mixer using a zkSNARK proof.
    ///
    /// `root`: The Merkle root the proof is against (big-endian [u8; 32]).
    /// `nullifier`: The nullifier to prevent double-spend (big-endian [u8; 32]).
    /// `proof`: The Groth16 proof, 256 bytes: A(64) || B(128) || C(64), all big-endian.
    pub fn withdraw(
        ctx: Context<WithdrawSol>,
        root: [u8; 32],
        nullifier: [u8; 32],
        proof: Vec<u8>,
    ) -> Result<()> {
        let pool = &mut ctx.accounts.pool;
        let nullifier_account = &mut ctx.accounts.nullifier_account;

        // Check nullifier hasn't been spent
        require!(!nullifier_account.is_spent, MiximusError::DoubleSpend);

        // Check root is known
        require!(
            pool.root_history.contains(&root),
            MiximusError::UnknownRoot
        );

        // Compute external hash binding proof to this recipient
        let ext_hash = compute_ext_hash(
            &ctx.accounts.pool_vault.key(),
            &ctx.accounts.recipient.key(),
        );

        // Verify Groth16 proof using alt_bn128 syscalls
        require!(
            verify_groth16_proof(&pool.vk_data, &root, &nullifier, &ext_hash, &proof),
            MiximusError::InvalidProof
        );

        // Mark nullifier as spent
        nullifier_account.is_spent = true;
        nullifier_account.nullifier = nullifier;

        // Transfer SOL from pool PDA to recipient
        let pool_vault_info = ctx.accounts.pool_vault.to_account_info();
        let recipient_info = ctx.accounts.recipient.to_account_info();
        **pool_vault_info.try_borrow_mut_lamports()? -= pool.denomination;
        **recipient_info.try_borrow_mut_lamports()? += pool.denomination;

        emit!(WithdrawEvent {
            nullifier,
            recipient: ctx.accounts.recipient.key(),
            timestamp: Clock::get()?.unix_timestamp,
        });

        Ok(())
    }
    /// Batch withdraw SOL — process up to 5 withdrawals in a single transaction.
    ///
    /// Each withdrawal verifies a separate Groth16 proof and marks a nullifier as spent.
    /// The total amount (denomination * count) is transferred to the recipient.
    pub fn batch_withdraw(
        ctx: Context<BatchWithdrawSol>,
        roots: Vec<[u8; 32]>,
        nullifiers: Vec<[u8; 32]>,
        proofs: Vec<Vec<u8>>,
    ) -> Result<()> {
        let pool = &mut ctx.accounts.pool;
        let count = roots.len();

        require!(count > 0 && count <= 5, MiximusError::InvalidBatchSize);
        require!(nullifiers.len() == count, MiximusError::InvalidBatchSize);
        require!(proofs.len() == count, MiximusError::InvalidBatchSize);

        for i in 0..count {
            // Check root is known
            require!(
                pool.root_history.contains(&roots[i]),
                MiximusError::UnknownRoot
            );

            // Compute external hash binding proof to this recipient
            let ext_hash = compute_ext_hash(
                &ctx.accounts.pool_vault.key(),
                &ctx.accounts.recipient.key(),
            );

            // Verify Groth16 proof using alt_bn128 syscalls
            require!(
                verify_groth16_proof(&pool.vk_data, &roots[i], &nullifiers[i], &ext_hash, &proofs[i]),
                MiximusError::InvalidProof
            );

            emit!(WithdrawEvent {
                nullifier: nullifiers[i],
                recipient: ctx.accounts.recipient.key(),
                timestamp: Clock::get()?.unix_timestamp,
            });
        }

        // Mark all nullifiers as spent via remaining_accounts (PDAs passed in)
        // Each nullifier account is passed as a remaining account
        let nullifier_accounts = &ctx.remaining_accounts;
        require!(nullifier_accounts.len() == count, MiximusError::InvalidBatchSize);

        for i in 0..count {
            let nullifier_info = &nullifier_accounts[i];
            let mut data = nullifier_info.try_borrow_mut_data()?;
            // Write is_spent = true (after 8-byte discriminator)
            if data.len() >= 41 {
                data[8] = 1; // is_spent = true
                data[9..41].copy_from_slice(&nullifiers[i]);
            }
        }

        // Transfer total SOL from pool PDA to recipient
        let total_amount = pool.denomination * count as u64;
        let pool_vault_info = ctx.accounts.pool_vault.to_account_info();
        let recipient_info = ctx.accounts.recipient.to_account_info();
        **pool_vault_info.try_borrow_mut_lamports()? -= total_amount;
        **recipient_info.try_borrow_mut_lamports()? += total_amount;

        Ok(())
    }
}

// =========================================================================
//                          ACCOUNT STRUCTURES
// =========================================================================

#[account]
pub struct MixerPool {
    /// The authority that created this pool
    pub authority: Pubkey,
    /// Fixed denomination in lamports
    pub denomination: u64,
    /// Next available leaf index in the Merkle tree
    pub next_leaf_index: u64,
    /// Current Merkle root (big-endian [u8; 32])
    pub current_root: [u8; 32],
    /// History of recent roots for withdrawal validation
    pub root_history: Vec<[u8; 32]>,
    /// Serialized verifying key (14 x 32 bytes + gammaABC)
    pub vk_data: Vec<u8>,
    /// PDA bump seed
    pub bump: u8,
}

/// Separate account for the full Merkle tree node storage.
/// This allows the tree to grow without being constrained by the MixerPool account size.
/// Each node is stored as (encoded_key -> hash) in a BTreeMap.
#[account]
pub struct TreeNodes {
    /// The MixerPool this tree belongs to
    pub pool: Pubkey,
    /// All tree nodes: key = encode_tree_key(level, index), value = hash (BE [u8;32])
    pub nodes: BTreeMap<u64, [u8; 32]>,
}

#[account]
pub struct NullifierAccount {
    pub is_spent: bool,
    pub nullifier: [u8; 32],
}

// =========================================================================
//                          INSTRUCTION CONTEXTS
// =========================================================================

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(
        init,
        payer = authority,
        space = 8 + 32 + 8 + 8 + 32 + 4 + (ROOT_HISTORY_SIZE * 32) + 4 + 2048 + 1,
        seeds = [b"mixer_pool", authority.key().as_ref()],
        bump
    )]
    pub pool: Account<'info, MixerPool>,
    #[account(
        init,
        payer = authority,
        space = 8 + 32 + 4 + 10_000_000,
        seeds = [b"tree_nodes", pool.key().as_ref()],
        bump
    )]
    pub tree_nodes: Account<'info, TreeNodes>,
    #[account(mut)]
    pub authority: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct DepositSol<'info> {
    #[account(mut)]
    pub pool: Account<'info, MixerPool>,
    #[account(
        mut,
        seeds = [b"tree_nodes", pool.key().as_ref()],
        bump
    )]
    pub tree_nodes: Account<'info, TreeNodes>,
    /// CHECK: PDA vault for holding SOL
    #[account(mut, seeds = [b"vault", pool.key().as_ref()], bump)]
    pub pool_vault: AccountInfo<'info>,
    #[account(mut)]
    pub depositor: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(root: [u8; 32], nullifier: [u8; 32])]
pub struct WithdrawSol<'info> {
    #[account(mut)]
    pub pool: Account<'info, MixerPool>,
    /// CHECK: PDA vault
    #[account(mut, seeds = [b"vault", pool.key().as_ref()], bump)]
    pub pool_vault: AccountInfo<'info>,
    #[account(
        init_if_needed,
        payer = fee_payer,
        space = 8 + 1 + 32,
        seeds = [b"nullifier", pool.key().as_ref(), nullifier.as_ref()],
        bump
    )]
    pub nullifier_account: Account<'info, NullifierAccount>,
    /// CHECK: Recipient of the withdrawal
    #[account(mut)]
    pub recipient: AccountInfo<'info>,
    #[account(mut)]
    pub fee_payer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct BatchWithdrawSol<'info> {
    #[account(mut)]
    pub pool: Account<'info, MixerPool>,
    /// CHECK: PDA vault
    #[account(mut, seeds = [b"vault", pool.key().as_ref()], bump)]
    pub pool_vault: AccountInfo<'info>,
    /// CHECK: Recipient of the batch withdrawal
    #[account(mut)]
    pub recipient: AccountInfo<'info>,
    #[account(mut)]
    pub fee_payer: Signer<'info>,
    pub system_program: Program<'info, System>,
    // Nullifier accounts are passed via remaining_accounts
}

// =========================================================================
//                               EVENTS
// =========================================================================

#[event]
pub struct DepositEvent {
    pub leaf_hash: [u8; 32],
    pub leaf_index: u64,
    pub timestamp: i64,
}

#[event]
pub struct WithdrawEvent {
    pub nullifier: [u8; 32],
    pub recipient: Pubkey,
    pub timestamp: i64,
}

// =========================================================================
//                              ERRORS
// =========================================================================

#[error_code]
pub enum MiximusError {
    #[msg("Merkle tree is full")]
    TreeFull,
    #[msg("Cannot double-spend: nullifier already used")]
    DoubleSpend,
    #[msg("Unknown Merkle root")]
    UnknownRoot,
    #[msg("Invalid zkSNARK proof")]
    InvalidProof,
    #[msg("Invalid denomination")]
    InvalidDenomination,
    #[msg("Invalid batch size (must be 1-5)")]
    InvalidBatchSize,
}
