/**
 * Miximus NEAR Protocol Contract
 *
 * zkSNARK-based mixer for NEAR native tokens.
 * Written in Rust, compiled to WebAssembly for the NEAR runtime.
 *
 * Implements:
 *   - MiMC-p/p cipher (x^7, 91 rounds, BN254 scalar field)
 *   - Miyaguchi-Preneel compression for MiMC hashing
 *   - Level-specific IVs matching the ethsnarks C++ circuit
 *   - Full Merkle tree with persistent storage of all nodes
 *   - Groth16 verification via NEAR alt_bn128 host functions
 *
 * Supported: NEAR (native)
 */

use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
use near_sdk::collections::LookupMap;
use near_sdk::{env, near_bindgen, AccountId, Balance, PanicOnDefault, Promise};

pub const TREE_DEPTH: usize = 29;
pub const MAX_LEAVES: u64 = 1 << TREE_DEPTH;

// =========================================================================
//                     BN254 SCALAR FIELD ARITHMETIC
// =========================================================================

/// BN254 scalar field modulus q:
/// 21888242871839275222246405745257275088548364400416034343698204186575808495617
/// In 4x u64 limbs (little-endian): [lo0, lo1, lo2, hi]
const Q: [u64; 4] = [
    0x43e1f593f0000001,
    0x2833e84879b97091,
    0xb85045b68181585d,
    0x30644e72e131a029,
];

/// R^2 mod q for Montgomery multiplication (precomputed)
/// Used for converting to Montgomery form.
const R2: [u64; 4] = [
    0x1bb8e645ae216da7,
    0x53fe3ab1e35c59e3,
    0x8c49833d53bb8085,
    0x0216d0b17f4e44a5,
];

/// Montgomery parameter: inv = -q^{-1} mod 2^64
const INV: u64 = 0xc2e1f593efffffff;

/// Represents a 256-bit unsigned integer as 4 x u64 limbs (little-endian).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct U256([u64; 4]);

impl U256 {
    const ZERO: U256 = U256([0, 0, 0, 0]);

    /// Create from a big-endian 32-byte array.
    fn from_be_bytes(bytes: &[u8; 32]) -> Self {
        let mut limbs = [0u64; 4];
        // bytes[0..8] is the most significant
        limbs[3] = u64::from_be_bytes([
            bytes[0], bytes[1], bytes[2], bytes[3],
            bytes[4], bytes[5], bytes[6], bytes[7],
        ]);
        limbs[2] = u64::from_be_bytes([
            bytes[8], bytes[9], bytes[10], bytes[11],
            bytes[12], bytes[13], bytes[14], bytes[15],
        ]);
        limbs[1] = u64::from_be_bytes([
            bytes[16], bytes[17], bytes[18], bytes[19],
            bytes[20], bytes[21], bytes[22], bytes[23],
        ]);
        limbs[0] = u64::from_be_bytes([
            bytes[24], bytes[25], bytes[26], bytes[27],
            bytes[28], bytes[29], bytes[30], bytes[31],
        ]);
        U256(limbs)
    }

    /// Serialize to big-endian 32-byte array.
    fn to_be_bytes(&self) -> [u8; 32] {
        let mut out = [0u8; 32];
        let b3 = self.0[3].to_be_bytes();
        let b2 = self.0[2].to_be_bytes();
        let b1 = self.0[1].to_be_bytes();
        let b0 = self.0[0].to_be_bytes();
        out[0..8].copy_from_slice(&b3);
        out[8..16].copy_from_slice(&b2);
        out[16..24].copy_from_slice(&b1);
        out[24..32].copy_from_slice(&b0);
        out
    }

    /// Serialize to little-endian 32-byte array (for alt_bn128 host functions).
    fn to_le_bytes(&self) -> [u8; 32] {
        let mut out = [0u8; 32];
        let b0 = self.0[0].to_le_bytes();
        let b1 = self.0[1].to_le_bytes();
        let b2 = self.0[2].to_le_bytes();
        let b3 = self.0[3].to_le_bytes();
        out[0..8].copy_from_slice(&b0);
        out[8..16].copy_from_slice(&b1);
        out[16..24].copy_from_slice(&b2);
        out[24..32].copy_from_slice(&b3);
        out
    }

    /// Create from a little-endian 32-byte array.
    fn from_le_bytes(bytes: &[u8; 32]) -> Self {
        let mut limbs = [0u64; 4];
        limbs[0] = u64::from_le_bytes([
            bytes[0], bytes[1], bytes[2], bytes[3],
            bytes[4], bytes[5], bytes[6], bytes[7],
        ]);
        limbs[1] = u64::from_le_bytes([
            bytes[8], bytes[9], bytes[10], bytes[11],
            bytes[12], bytes[13], bytes[14], bytes[15],
        ]);
        limbs[2] = u64::from_le_bytes([
            bytes[16], bytes[17], bytes[18], bytes[19],
            bytes[20], bytes[21], bytes[22], bytes[23],
        ]);
        limbs[3] = u64::from_le_bytes([
            bytes[24], bytes[25], bytes[26], bytes[27],
            bytes[28], bytes[29], bytes[30], bytes[31],
        ]);
        U256(limbs)
    }

    /// Returns true if self >= Q.
    fn gte_q(&self) -> bool {
        for i in (0..4).rev() {
            if self.0[i] > Q[i] {
                return true;
            }
            if self.0[i] < Q[i] {
                return false;
            }
        }
        true // equal
    }

    /// Subtraction: self - other, assuming self >= other. No modular reduction.
    fn sub_no_reduce(&self, other: &[u64; 4]) -> U256 {
        let mut result = [0u64; 4];
        let mut borrow: u64 = 0;
        for i in 0..4 {
            let (diff, b1) = self.0[i].overflowing_sub(other[i]);
            let (diff2, b2) = diff.overflowing_sub(borrow);
            result[i] = diff2;
            borrow = (b1 as u64) + (b2 as u64);
        }
        U256(result)
    }

    /// Addition modulo Q.
    fn addmod_q(&self, other: &U256) -> U256 {
        let mut result = [0u64; 4];
        let mut carry: u64 = 0;
        for i in 0..4 {
            let sum = (self.0[i] as u128) + (other.0[i] as u128) + (carry as u128);
            result[i] = sum as u64;
            carry = (sum >> 64) as u64;
        }
        let mut r = U256(result);
        // If result >= Q or there was a carry, subtract Q
        if carry > 0 || r.gte_q() {
            r = r.sub_no_reduce(&Q);
        }
        r
    }

    /// Convert to Montgomery form: aR mod q
    fn to_montgomery(&self) -> U256 {
        mont_mul(self, &U256(R2))
    }

    /// Convert from Montgomery form: a * R^{-1} mod q
    fn from_montgomery(&self) -> U256 {
        mont_mul(self, &U256([1, 0, 0, 0]))
    }
}

/// Montgomery multiplication: (a * b * R^{-1}) mod q
/// Both inputs should be in Montgomery form for field multiplication.
fn mont_mul(a: &U256, b: &U256) -> U256 {
    // Schoolbook multiplication with Montgomery reduction
    let mut t = [0u64; 8]; // 512-bit intermediate

    // Multiply
    for i in 0..4 {
        let mut carry: u64 = 0;
        for j in 0..4 {
            let prod = (a.0[i] as u128) * (b.0[j] as u128)
                + (t[i + j] as u128)
                + (carry as u128);
            t[i + j] = prod as u64;
            carry = (prod >> 64) as u64;
        }
        t[i + 4] = carry;
    }

    // Montgomery reduction
    for i in 0..4 {
        let m = t[i].wrapping_mul(INV);
        let mut carry: u64 = 0;
        for j in 0..4 {
            let prod = (m as u128) * (Q[j] as u128)
                + (t[i + j] as u128)
                + (carry as u128);
            t[i + j] = prod as u64;
            carry = (prod >> 64) as u64;
        }
        // Propagate carry
        let mut k = i + 4;
        while carry > 0 && k < 8 {
            let sum = (t[k] as u128) + (carry as u128);
            t[k] = sum as u64;
            carry = (sum >> 64) as u64;
            k += 1;
        }
    }

    let mut result = U256([t[4], t[5], t[6], t[7]]);
    if result.gte_q() {
        result = result.sub_no_reduce(&Q);
    }
    result
}

/// Field multiplication: (a * b) mod q, both in Montgomery form.
fn field_mul(a: &U256, b: &U256) -> U256 {
    mont_mul(a, b)
}

// =========================================================================
//                       MIMC CIPHER & HASH
// =========================================================================

/// Number of MiMC rounds.
const MIMC_ROUNDS: usize = 91;

/// MiMC-p/p cipher with exponent x^7, 91 rounds over BN254 scalar field.
///
/// Round constants are derived from a keccak256 hash chain starting with
/// keccak256("mimc"). Each round: c_{i+1} = keccak256(c_i).
///
/// Cipher(x, k):
///   for each round i:
///     c = next_round_constant
///     t = x + c + k   (mod q)
///     x = t^7          (mod q)
///   return x + k
fn mimc_cipher(in_x: &U256, in_k: &U256) -> U256 {
    // Generate the initial seed for round constants: keccak256("mimc")
    let seed = env::keccak256(b"mimc");

    // Convert inputs to Montgomery form for efficient field multiplication
    let k_mont = in_k.to_montgomery();
    let mut x_mont = in_x.to_montgomery();

    let mut c_bytes = [0u8; 32];
    c_bytes.copy_from_slice(&seed);

    for _ in 0..MIMC_ROUNDS {
        // Advance the hash chain: c = keccak256(c)
        let hash = env::keccak256(&c_bytes);
        c_bytes.copy_from_slice(&hash);

        // Parse round constant as big-endian U256, reduce mod q, convert to Montgomery
        let c_val = U256::from_be_bytes(&c_bytes);
        let c_reduced = reduce_mod_q(&c_val);
        let c_mont = c_reduced.to_montgomery();

        // t = x + c + k (mod q)
        let t = x_mont.addmod_q(&c_mont).addmod_q(&k_mont);

        // t^7 = t * (t^2)^3
        let t2 = field_mul(&t, &t);       // t^2
        let t4 = field_mul(&t2, &t2);      // t^4
        let t6 = field_mul(&t4, &t2);      // t^6
        let t7 = field_mul(&t6, &t);       // t^7
        x_mont = t7;
    }

    // Final key addition: result = x + k (mod q)
    let result_mont = x_mont.addmod_q(&k_mont);

    // Convert back from Montgomery form
    result_mont.from_montgomery()
}

/// Reduce a U256 modulo Q. For values that may be >= Q (e.g. keccak output).
fn reduce_mod_q(val: &U256) -> U256 {
    let mut r = *val;
    // keccak256 output is 256 bits, Q is ~254 bits, so at most a few subtractions needed.
    while r.gte_q() {
        r = r.sub_no_reduce(&Q);
    }
    r
}

/// MiMC hash using Miyaguchi-Preneel compression.
///
/// hash(x_0, x_1, ..., x_{n-1}):
///   r = iv
///   for each x_i:
///     h = cipher(x_i, r)
///     r = r + x_i + h   (mod q)
///   return r
fn mimc_hash(data: &[U256], iv: &U256) -> U256 {
    let mut r = *iv;
    for x in data {
        let h = mimc_cipher(x, &r);
        // Miyaguchi-Preneel: r = r + x + cipher(x, r)
        r = r.addmod_q(x).addmod_q(&h);
    }
    r
}

/// MiMC hash with IV = 0 (used for public input hashing and leaf hashing).
fn mimc_hash_default(data: &[U256]) -> U256 {
    mimc_hash(data, &U256::ZERO)
}

/// Convert a 32-byte big-endian hash to a U256, then compute MiMC hash of two field
/// elements (used for Merkle tree operations with level IV).
fn mimc_hash_pair_with_iv(left: &[u8; 32], right: &[u8; 32], iv: &U256) -> [u8; 32] {
    let l = U256::from_be_bytes(left);
    let r = U256::from_be_bytes(right);
    let result = mimc_hash(&[l, r], iv);
    result.to_be_bytes()
}

// =========================================================================
//                         LEVEL IVs
// =========================================================================

/// Level-specific initialization vectors for Merkle tree hashing.
/// These match the ethsnarks C++ circuit exactly.
const LEVEL_IVS: [&str; 29] = [
    "149674538925118052205057075966660054952481571156186698930522557832224430770",
    "9670701465464311903249220692483401938888498641874948577387207195814981706974",
    "18318710344500308168304415114839554107298291987930233567781901093928276468271",
    "6597209388525824933845812104623007130464197923269180086306970975123437805179",
    "21720956803147356712695575768577036859892220417043839172295094119877855004262",
    "10330261616520855230513677034606076056972336573153777401182178891807369896722",
    "17466547730316258748333298168566143799241073466140136663575045164199607937939",
    "18881017304615283094648494495339883533502299318365959655029893746755475886610",
    "21580915712563378725413940003372103925756594604076607277692074507345076595494",
    "12316305934357579015754723412431647910012873427291630993042374701002287130550",
    "18905410889238873726515380969411495891004493295170115920825550288019118582494",
    "12819107342879320352602391015489840916114959026915005817918724958237245903353",
    "8245796392944118634696709403074300923517437202166861682117022548371601758802",
    "16953062784314687781686527153155644849196472783922227794465158787843281909585",
    "19346880451250915556764413197424554385509847473349107460608536657852472800734",
    "14486794857958402714787584825989957493343996287314210390323617462452254101347",
    "11127491343750635061768291849689189917973916562037173191089384809465548650641",
    "12217916643258751952878742936579902345100885664187835381214622522318889050675",
    "722025110834410790007814375535296040832778338853544117497481480537806506496",
    "15115624438829798766134408951193645901537753720219896384705782209102859383951",
    "11495230981884427516908372448237146604382590904456048258839160861769955046544",
    "16867999085723044773810250829569850875786210932876177117428755424200948460050",
    "1884116508014449609846749684134533293456072152192763829918284704109129550542",
    "14643335163846663204197941112945447472862168442334003800621296569318670799451",
    "1933387276732345916104540506251808516402995586485132246682941535467305930334",
    "7286414555941977227951257572976885370489143210539802284740420664558593616067",
    "16932161189449419608528042274282099409408565503929504242784173714823499212410",
    "16562533130736679030886586765487416082772837813468081467237161865787494093536",
    "6037428193077828806710267464232314380014232668931818917272972397574634037180",
];

/// Parse a decimal string into a U256.
fn u256_from_decimal(s: &str) -> U256 {
    // Parse decimal string into 4 x u64 limbs (little-endian)
    // We do grade-school multiplication: process each digit.
    let mut limbs = [0u64; 4];
    for ch in s.bytes() {
        let digit = (ch - b'0') as u64;
        // Multiply limbs by 10
        let mut carry: u64 = 0;
        for limb in limbs.iter_mut() {
            let prod = (*limb as u128) * 10 + (carry as u128);
            *limb = prod as u64;
            carry = (prod >> 64) as u64;
        }
        // Add digit
        let sum = (limbs[0] as u128) + (digit as u128);
        limbs[0] = sum as u64;
        let mut c = (sum >> 64) as u64;
        for i in 1..4 {
            if c == 0 {
                break;
            }
            let s = (limbs[i] as u128) + (c as u128);
            limbs[i] = s as u64;
            c = (s >> 64) as u64;
        }
    }
    U256(limbs)
}

/// Get the level IV for a given Merkle tree level.
fn level_iv(level: usize) -> U256 {
    assert!(level < TREE_DEPTH, "Invalid level");
    u256_from_decimal(LEVEL_IVS[level])
}

// =========================================================================
//                     MERKLE TREE (full storage)
// =========================================================================

/// Compute zero hashes for each level of the Merkle tree.
/// zero_hashes[0] = 0 (empty leaf)
/// zero_hashes[i+1] = MiMC_iv(zero_hashes[i], zero_hashes[i], level_iv(i))
fn compute_zero_hashes() -> [[u8; 32]; TREE_DEPTH + 1] {
    let mut zeros = [[0u8; 32]; TREE_DEPTH + 1];
    // zeros[0] = 0 (all zero bytes = field element 0)
    for i in 0..TREE_DEPTH {
        let iv = level_iv(i);
        zeros[i + 1] = mimc_hash_pair_with_iv(&zeros[i], &zeros[i], &iv);
    }
    zeros
}

// =========================================================================
//                    GROTH16 VERIFICATION (NEAR alt_bn128)
// =========================================================================

/// BN254 base field modulus P (for G1 point negation).
/// 21888242871839275222246405745257275088696311157297823662689037894645226208583
const P: [u64; 4] = [
    0x3c208c16d87cfd47,
    0x97816a916871ca8d,
    0xb85045b68181585d,
    0x30644e72e131a029,
];

/// Negate a G1 point's y coordinate: y_neg = P - y (in base field).
fn negate_g1_y(y_le: &[u8; 32]) -> [u8; 32] {
    let y = U256::from_le_bytes(y_le);
    // Check for point at infinity (y == 0)
    if y == U256::ZERO {
        return [0u8; 32];
    }
    // Compute P - y directly
    let mut result = [0u64; 4];
    let mut borrow: u64 = 0;
    for i in 0..4 {
        let (diff, b1) = P[i].overflowing_sub(y.0[i]);
        let (diff2, b2) = diff.overflowing_sub(borrow);
        result[i] = diff2;
        borrow = (b1 as u64) + (b2 as u64);
    }
    U256(result).to_le_bytes()
}

/// Verifying key structure for Groth16 on BN254.
///
/// The VK is stored as a byte array with the following layout (each uint256 is 32 bytes BE):
///   vk[0..2]   = alpha (G1): x, y
///   vk[2..6]   = beta (G2): x_c1, x_c0, y_c1, y_c0
///   vk[6..10]  = gamma (G2): x_c1, x_c0, y_c1, y_c0
///   vk[10..14] = delta (G2): x_c1, x_c0, y_c1, y_c0
///   vk[14..]   = gammaABC (G1 points): pairs of (x, y) in 32-byte BE
///
/// Total: 14 * 32 bytes for the fixed VK, then variable gammaABC.
///
/// NEAR alt_bn128 functions use LITTLE-ENDIAN encoding for field elements.

/// Parse a 32-byte big-endian uint256 from VK data at the given offset.
fn parse_vk_element(vk_data: &[u8], index: usize) -> [u8; 32] {
    let offset = index * 32;
    let mut buf = [0u8; 32];
    buf.copy_from_slice(&vk_data[offset..offset + 32]);
    buf
}

/// Convert a 32-byte big-endian value to little-endian.
fn be_to_le(be: &[u8; 32]) -> [u8; 32] {
    let mut le = [0u8; 32];
    for i in 0..32 {
        le[i] = be[31 - i];
    }
    le
}

/// Verify a Groth16 proof using NEAR alt_bn128 host functions.
///
/// Proof format: 8 x uint256 big-endian (256 bytes total):
///   proof[0..2] = A (G1): x, y
///   proof[2..6] = B (G2): x_c1, x_c0, y_c1, y_c0
///   proof[6..8] = C (G1): x, y
///
/// Public input: a single scalar = MiMC(root, nullifier, ext_hash)
///
/// Verification equation (4-pair pairing check):
///   e(A, B) * e(-alpha, beta) * e(-vk_x, gamma) * e(-C, delta) == 1
///
/// Where vk_x = gammaABC[0] + public_input * gammaABC[1]
fn verify_groth16_proof(
    vk_data: &[u8],
    root: &[u8],
    nullifier: &[u8],
    proof: &[u8],
    recipient: &AccountId,
) -> bool {
    // Proof must be 8 * 32 = 256 bytes
    if proof.len() != 256 {
        env::log_str("Proof must be 256 bytes (8 x uint256)");
        return false;
    }

    // VK must have at least 14 * 32 = 448 bytes (fixed part) + 2 gammaABC points (128 bytes)
    if vk_data.len() < 14 * 32 + 4 * 32 {
        env::log_str("VK data too short");
        return false;
    }

    // --- Compute public input ---
    // ext_hash = sha256(contract_account_id || recipient) mod q
    let contract_id = env::current_account_id();
    let mut ext_data = Vec::new();
    ext_data.extend_from_slice(contract_id.as_bytes());
    ext_data.extend_from_slice(recipient.as_bytes());
    let ext_sha = env::sha256(&ext_data);
    let mut ext_bytes = [0u8; 32];
    ext_bytes.copy_from_slice(&ext_sha);
    let ext_hash = reduce_mod_q(&U256::from_be_bytes(&ext_bytes));

    // Parse root and nullifier as field elements
    let mut root_arr = [0u8; 32];
    let mut null_arr = [0u8; 32];
    // Pad to 32 bytes if needed
    let rlen = root.len().min(32);
    root_arr[32 - rlen..].copy_from_slice(&root[..rlen]);
    let nlen = nullifier.len().min(32);
    null_arr[32 - nlen..].copy_from_slice(&nullifier[..nlen]);
    let root_val = U256::from_be_bytes(&root_arr);
    let null_val = U256::from_be_bytes(&null_arr);

    // public_input = MiMC(root, nullifier, ext_hash) with IV = 0
    let pub_input = mimc_hash_default(&[root_val, null_val, ext_hash]);

    // --- Parse proof points ---
    // All proof/VK elements are big-endian uint256s. NEAR alt_bn128 uses little-endian.

    // Proof A (G1)
    let a_x_be = parse_vk_element(proof, 0);
    let a_y_be = parse_vk_element(proof, 1);
    let a_x_le = be_to_le(&a_x_be);
    let a_y_le = be_to_le(&a_y_be);

    // Proof B (G2) - Note: G2 points in EVM format are (x_c1, x_c0, y_c1, y_c0)
    // but NEAR alt_bn128 expects (x_c0, x_c1, y_c0, y_c1) in little-endian
    let b_x_c1_be = parse_vk_element(proof, 2);
    let b_x_c0_be = parse_vk_element(proof, 3);
    let b_y_c1_be = parse_vk_element(proof, 4);
    let b_y_c0_be = parse_vk_element(proof, 5);

    // Proof C (G1)
    let c_x_be = parse_vk_element(proof, 6);
    let c_y_be = parse_vk_element(proof, 7);
    let c_x_le = be_to_le(&c_x_be);
    let c_y_le = be_to_le(&c_y_be);

    // --- Parse VK ---
    // alpha (G1)
    let alpha_x_le = be_to_le(&parse_vk_element(vk_data, 0));
    let alpha_y_le = be_to_le(&parse_vk_element(vk_data, 1));

    // beta (G2)
    let beta_x_c1_be = parse_vk_element(vk_data, 2);
    let beta_x_c0_be = parse_vk_element(vk_data, 3);
    let beta_y_c1_be = parse_vk_element(vk_data, 4);
    let beta_y_c0_be = parse_vk_element(vk_data, 5);

    // gamma (G2)
    let gamma_x_c1_be = parse_vk_element(vk_data, 6);
    let gamma_x_c0_be = parse_vk_element(vk_data, 7);
    let gamma_y_c1_be = parse_vk_element(vk_data, 8);
    let gamma_y_c0_be = parse_vk_element(vk_data, 9);

    // delta (G2)
    let delta_x_c1_be = parse_vk_element(vk_data, 10);
    let delta_x_c0_be = parse_vk_element(vk_data, 11);
    let delta_y_c1_be = parse_vk_element(vk_data, 12);
    let delta_y_c0_be = parse_vk_element(vk_data, 13);

    // gammaABC[0] (G1)
    let gamma_abc_0_x_le = be_to_le(&parse_vk_element(vk_data, 14));
    let gamma_abc_0_y_le = be_to_le(&parse_vk_element(vk_data, 15));

    // gammaABC[1] (G1)
    let gamma_abc_1_x_le = be_to_le(&parse_vk_element(vk_data, 16));
    let gamma_abc_1_y_le = be_to_le(&parse_vk_element(vk_data, 17));

    // --- Compute vk_x = gammaABC[0] + public_input * gammaABC[1] ---

    // Step 1: Scalar multiplication: public_input * gammaABC[1]
    // alt_bn128_g1_multiexp input format: sequence of (point, scalar) pairs
    // Each point is 64 bytes (x_le, y_le), each scalar is 32 bytes (le)
    let scalar_le = pub_input.to_le_bytes();

    let mut multiexp_input = Vec::with_capacity(96);
    multiexp_input.extend_from_slice(&gamma_abc_1_x_le);
    multiexp_input.extend_from_slice(&gamma_abc_1_y_le);
    multiexp_input.extend_from_slice(&scalar_le);

    let mul_result = env::alt_bn128_g1_multiexp(&multiexp_input);
    // mul_result is 64 bytes: (x_le, y_le)
    if mul_result.len() != 64 {
        env::log_str("G1 multiexp returned unexpected length");
        return false;
    }

    // Step 2: Point addition: gammaABC[0] + (public_input * gammaABC[1])
    // alt_bn128_g1_sum input: sequence of (sign, point) where sign is 0 for add, 1 for sub
    // Actually, NEAR's alt_bn128_g1_sum takes a flat sequence of points to add.
    // Format: 1-byte sign (0=add, 1=negate) + 64-byte point, repeated.
    let mut sum_input = Vec::with_capacity(130); // 2 * (1 + 64)
    // Add gammaABC[0]
    sum_input.push(0u8); // sign: add
    sum_input.extend_from_slice(&gamma_abc_0_x_le);
    sum_input.extend_from_slice(&gamma_abc_0_y_le);
    // Add the multiexp result
    sum_input.push(0u8); // sign: add
    sum_input.extend_from_slice(&mul_result);

    let vk_x_bytes = env::alt_bn128_g1_sum(&sum_input);
    if vk_x_bytes.len() != 64 {
        env::log_str("G1 sum returned unexpected length");
        return false;
    }

    let mut vk_x_x_le = [0u8; 32];
    let mut vk_x_y_le = [0u8; 32];
    vk_x_x_le.copy_from_slice(&vk_x_bytes[0..32]);
    vk_x_y_le.copy_from_slice(&vk_x_bytes[32..64]);

    // --- Build pairing check input ---
    // NEAR alt_bn128_pairing_check expects pairs of (G1, G2) points.
    // G1 = 64 bytes (x_le, y_le)
    // G2 = 128 bytes (x_c0_le, x_c1_le, y_c0_le, y_c1_le)
    // Total per pair: 192 bytes. 4 pairs = 768 bytes.
    //
    // Equation: e(A, B) * e(-alpha, beta) * e(-vk_x, gamma) * e(-C, delta) == 1

    let mut pairing_input = Vec::with_capacity(768);

    // Pair 1: (A, B)
    pairing_input.extend_from_slice(&a_x_le);
    pairing_input.extend_from_slice(&a_y_le);
    pairing_input.extend_from_slice(&be_to_le(&b_x_c0_be));
    pairing_input.extend_from_slice(&be_to_le(&b_x_c1_be));
    pairing_input.extend_from_slice(&be_to_le(&b_y_c0_be));
    pairing_input.extend_from_slice(&be_to_le(&b_y_c1_be));

    // Pair 2: (-alpha, beta)
    let neg_alpha_y_le = negate_g1_y(&alpha_y_le);
    pairing_input.extend_from_slice(&alpha_x_le);
    pairing_input.extend_from_slice(&neg_alpha_y_le);
    pairing_input.extend_from_slice(&be_to_le(&beta_x_c0_be));
    pairing_input.extend_from_slice(&be_to_le(&beta_x_c1_be));
    pairing_input.extend_from_slice(&be_to_le(&beta_y_c0_be));
    pairing_input.extend_from_slice(&be_to_le(&beta_y_c1_be));

    // Pair 3: (-vk_x, gamma)
    let neg_vk_x_y_le = negate_g1_y(&vk_x_y_le);
    pairing_input.extend_from_slice(&vk_x_x_le);
    pairing_input.extend_from_slice(&neg_vk_x_y_le);
    pairing_input.extend_from_slice(&be_to_le(&gamma_x_c0_be));
    pairing_input.extend_from_slice(&be_to_le(&gamma_x_c1_be));
    pairing_input.extend_from_slice(&be_to_le(&gamma_y_c0_be));
    pairing_input.extend_from_slice(&be_to_le(&gamma_y_c1_be));

    // Pair 4: (-C, delta)
    let neg_c_y_le = negate_g1_y(&c_y_le);
    pairing_input.extend_from_slice(&c_x_le);
    pairing_input.extend_from_slice(&neg_c_y_le);
    pairing_input.extend_from_slice(&be_to_le(&delta_x_c0_be));
    pairing_input.extend_from_slice(&be_to_le(&delta_x_c1_be));
    pairing_input.extend_from_slice(&be_to_le(&delta_y_c0_be));
    pairing_input.extend_from_slice(&be_to_le(&delta_y_c1_be));

    // Execute pairing check: returns true if the product of pairings equals 1
    env::alt_bn128_pairing_check(&pairing_input)
}

// =========================================================================
//                          CONTRACT STATE
// =========================================================================

#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize, PanicOnDefault)]
pub struct MiximusNear {
    owner: AccountId,
    denomination: Balance,
    asset_symbol: String,
    next_leaf_index: u64,
    current_root: Vec<u8>,
    /// Full Merkle tree node storage: key = (level, index) -> 32-byte hash
    tree_nodes: LookupMap<(u32, u64), Vec<u8>>,
    /// Precomputed zero hashes for each level (uninitialized nodes)
    zero_hashes: Vec<Vec<u8>>,
    roots: LookupMap<Vec<u8>, bool>,
    nullifiers: LookupMap<Vec<u8>, bool>,
    vk_data: Vec<u8>,
}

// =========================================================================
//                        CONTRACT METHODS
// =========================================================================

#[near_bindgen]
impl MiximusNear {
    #[init]
    pub fn new(denomination: Balance, asset_symbol: String, vk_data: Vec<u8>) -> Self {
        assert!(!env::state_exists(), "Already initialized");
        assert!(denomination > 0, "Denomination must be > 0");

        let mut roots = LookupMap::new(b"r");
        let tree_nodes = LookupMap::new(b"t");

        // Compute zero hashes for each level using MiMC with level IVs
        let zeros = compute_zero_hashes();
        let mut zero_hashes_storage: Vec<Vec<u8>> = Vec::with_capacity(TREE_DEPTH + 1);
        for i in 0..=TREE_DEPTH {
            zero_hashes_storage.push(zeros[i].to_vec());
        }

        // The initial root is zeros[TREE_DEPTH] (the root of a tree with all-zero leaves)
        let initial_root = zeros[TREE_DEPTH].to_vec();
        roots.insert(&initial_root, &true);

        Self {
            owner: env::predecessor_account_id(),
            denomination,
            asset_symbol,
            next_leaf_index: 0,
            current_root: initial_root,
            tree_nodes,
            zero_hashes: zero_hashes_storage,
            roots,
            nullifiers: LookupMap::new(b"n"),
            vk_data,
        }
    }

    /// Deposit NEAR into the mixer
    #[payable]
    pub fn deposit(&mut self, leaf_hash: String) -> (String, u64) {
        let attached = env::attached_deposit();
        assert_eq!(
            attached, self.denomination,
            "Must deposit exact denomination"
        );
        assert!(self.next_leaf_index < MAX_LEAVES, "Merkle tree is full");

        let leaf_bytes = hex::decode(&leaf_hash).expect("Invalid leaf hex");
        assert_eq!(leaf_bytes.len(), 32, "Leaf hash must be 32 bytes");

        let leaf_index = self.next_leaf_index;
        self.next_leaf_index += 1;

        let new_root = self.insert_leaf(&leaf_bytes, leaf_index);
        self.current_root = new_root.clone();
        self.roots.insert(&new_root, &true);

        env::log_str(&format!(
            "Deposit: leaf_index={}, leaf_hash={}",
            leaf_index, leaf_hash
        ));

        (hex::encode(&new_root), leaf_index)
    }

    /// Batch deposit NEAR — deposit N units in a single transaction
    #[payable]
    pub fn batch_deposit(&mut self, leaf_hashes: Vec<String>) -> Vec<(String, u64)> {
        let count = leaf_hashes.len();
        assert!(count > 0 && count <= 20, "Batch size must be 1-20");

        let total_required = self.denomination * count as u128;
        let attached = env::attached_deposit();
        assert_eq!(attached, total_required, "Must deposit exact total denomination");

        let mut results = Vec::new();
        for leaf_hash in &leaf_hashes {
            assert!(self.next_leaf_index < MAX_LEAVES, "Merkle tree is full");

            let leaf_bytes = hex::decode(leaf_hash).expect("Invalid leaf hex");
            assert_eq!(leaf_bytes.len(), 32, "Leaf hash must be 32 bytes");

            let leaf_index = self.next_leaf_index;
            self.next_leaf_index += 1;

            let new_root = self.insert_leaf(&leaf_bytes, leaf_index);
            self.current_root = new_root.clone();
            self.roots.insert(&new_root, &true);

            env::log_str(&format!(
                "Deposit: leaf_index={}, leaf_hash={}",
                leaf_index, leaf_hash
            ));

            results.push((hex::encode(&new_root), leaf_index));
        }
        results
    }

    /// Withdraw NEAR using zkSNARK proof
    pub fn withdraw(&mut self, root: String, nullifier: String, proof: Vec<u8>) {
        let recipient = env::predecessor_account_id();
        self.process_withdraw(&root, &nullifier, &proof, &recipient);

        Promise::new(recipient).transfer(self.denomination);
    }

    /// Batch withdraw NEAR — process up to 5 withdrawals in a single transaction
    pub fn batch_withdraw(&mut self, roots: Vec<String>, nullifiers: Vec<String>, proofs: Vec<Vec<u8>>) {
        let count = roots.len();
        assert!(count > 0 && count <= 5, "Batch size must be 1-5");
        assert!(nullifiers.len() == count, "Nullifiers length mismatch");
        assert!(proofs.len() == count, "Proofs length mismatch");

        let recipient = env::predecessor_account_id();

        for i in 0..count {
            self.process_withdraw(&roots[i], &nullifiers[i], &proofs[i], &recipient);
        }

        // Transfer total amount in a single transfer
        let total_amount = self.denomination * count as u128;
        Promise::new(recipient).transfer(total_amount);
    }

    /// Withdraw via relayer to a specified recipient
    pub fn withdraw_via_relayer(
        &mut self,
        root: String,
        nullifier: String,
        proof: Vec<u8>,
        recipient: AccountId,
        relayer_fee: Balance,
    ) {
        assert!(relayer_fee < self.denomination, "Fee exceeds denomination");
        self.process_withdraw(&root, &nullifier, &proof, &recipient);

        let relayer = env::predecessor_account_id();
        if relayer_fee > 0 {
            Promise::new(relayer).transfer(relayer_fee);
        }
        Promise::new(recipient).transfer(self.denomination - relayer_fee);
    }

    // View methods
    pub fn get_root(&self) -> String {
        hex::encode(&self.current_root)
    }

    pub fn is_spent(&self, nullifier: String) -> bool {
        let key = hex::decode(&nullifier).unwrap_or_default();
        self.nullifiers.get(&key).unwrap_or(false)
    }

    pub fn get_denomination(&self) -> Balance {
        self.denomination
    }

    pub fn get_next_leaf_index(&self) -> u64 {
        self.next_leaf_index
    }

    pub fn is_known_root(&self, root: String) -> bool {
        let key = hex::decode(&root).unwrap_or_default();
        self.roots.get(&key).unwrap_or(false)
    }

    /// Retrieve the Merkle authentication path for a given leaf index.
    /// Returns the sibling hashes and address bits for proof generation.
    pub fn get_path(&self, leaf_index: u64) -> (Vec<String>, Vec<bool>) {
        assert!(leaf_index < self.next_leaf_index, "Leaf not yet inserted");

        let mut path = Vec::with_capacity(TREE_DEPTH);
        let mut address_bits = Vec::with_capacity(TREE_DEPTH);

        for level in 0..TREE_DEPTH {
            let node_idx = leaf_index >> level;
            address_bits.push(node_idx & 1 == 1);
            let sibling_idx = node_idx ^ 1;
            let sibling = self.get_node(level as u32, sibling_idx);
            path.push(hex::encode(&sibling));
        }

        (path, address_bits)
    }

    /// Compute the leaf hash from a secret (MiMC hash with default IV).
    pub fn make_leaf_hash(&self, secret: String) -> String {
        let secret_bytes = hex::decode(&secret).expect("Invalid secret hex");
        assert_eq!(secret_bytes.len(), 32, "Secret must be 32 bytes");
        let mut arr = [0u8; 32];
        arr.copy_from_slice(&secret_bytes);
        let secret_val = U256::from_be_bytes(&arr);
        let leaf = mimc_hash_default(&[secret_val]);
        hex::encode(leaf.to_be_bytes())
    }

    /// Compute the external hash binding proof to contract + recipient.
    pub fn get_ext_hash(&self, recipient: AccountId) -> String {
        let contract_id = env::current_account_id();
        let mut data = Vec::new();
        data.extend_from_slice(contract_id.as_bytes());
        data.extend_from_slice(recipient.as_bytes());
        let sha = env::sha256(&data);
        let mut sha_arr = [0u8; 32];
        sha_arr.copy_from_slice(&sha);
        let ext = reduce_mod_q(&U256::from_be_bytes(&sha_arr));
        hex::encode(ext.to_be_bytes())
    }

    /// Hash public inputs using MiMC (for off-chain verification).
    pub fn hash_public_inputs(&self, root: String, nullifier: String, ext_hash: String) -> String {
        let root_bytes = hex::decode(&root).expect("Invalid root hex");
        let null_bytes = hex::decode(&nullifier).expect("Invalid nullifier hex");
        let ext_bytes = hex::decode(&ext_hash).expect("Invalid ext_hash hex");

        let mut r = [0u8; 32];
        let mut n = [0u8; 32];
        let mut e = [0u8; 32];
        r.copy_from_slice(&root_bytes);
        n.copy_from_slice(&null_bytes);
        e.copy_from_slice(&ext_bytes);

        let result = mimc_hash_default(&[
            U256::from_be_bytes(&r),
            U256::from_be_bytes(&n),
            U256::from_be_bytes(&e),
        ]);
        hex::encode(result.to_be_bytes())
    }

    // =========================================================================
    //                        INTERNAL METHODS
    // =========================================================================

    fn process_withdraw(
        &mut self,
        root: &str,
        nullifier: &str,
        proof: &[u8],
        recipient: &AccountId,
    ) {
        let nullifier_bytes = hex::decode(nullifier).expect("Invalid nullifier hex");
        let root_bytes = hex::decode(root).expect("Invalid root hex");

        assert!(
            !self.nullifiers.get(&nullifier_bytes).unwrap_or(false),
            "Cannot double-spend"
        );
        assert!(
            self.roots.get(&root_bytes).unwrap_or(false),
            "Unknown merkle root"
        );

        // Verify Groth16 proof using NEAR alt_bn128 host functions
        assert!(
            verify_groth16_proof(&self.vk_data, &root_bytes, &nullifier_bytes, proof, recipient),
            "Invalid zkSNARK proof"
        );

        self.nullifiers.insert(&nullifier_bytes, &true);

        env::log_str(&format!(
            "Withdrawal: nullifier={}, recipient={}",
            nullifier, recipient
        ));
    }

    /// Insert a leaf into the full Merkle tree, storing all intermediate nodes.
    fn insert_leaf(&mut self, leaf: &[u8], index: u64) -> Vec<u8> {
        assert_eq!(leaf.len(), 32, "Leaf must be 32 bytes");
        let mut current = [0u8; 32];
        current.copy_from_slice(leaf);

        // Store the leaf at level 0
        self.tree_nodes.insert(&(0, index), &current.to_vec());

        let mut idx = index;
        for level in 0..TREE_DEPTH {
            let parent_idx = idx / 2;

            let (left, right) = if idx % 2 == 0 {
                // Current node is left child; sibling is right child
                let sibling = self.get_node(level as u32, idx + 1);
                let mut sib = [0u8; 32];
                sib.copy_from_slice(&sibling);
                (current, sib)
            } else {
                // Current node is right child; sibling is left child
                let sibling = self.get_node(level as u32, idx - 1);
                let mut sib = [0u8; 32];
                sib.copy_from_slice(&sibling);
                (sib, current)
            };

            let iv = level_iv(level);
            current = mimc_hash_pair_with_iv(&left, &right, &iv);

            // Store the parent node at the next level
            self.tree_nodes
                .insert(&((level + 1) as u32, parent_idx), &current.to_vec());

            idx = parent_idx;
        }

        current.to_vec()
    }

    /// Get a node from the tree, returning the zero hash for that level if not present.
    fn get_node(&self, level: u32, index: u64) -> Vec<u8> {
        match self.tree_nodes.get(&(level, index)) {
            Some(val) => val,
            None => self.zero_hashes[level as usize].clone(),
        }
    }
}
