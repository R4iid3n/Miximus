//! Miximus Stellar Smart Contract (Soroban)
//!
//! zkSNARK-based mixer for Stellar (XLM).
//! Written in Rust for Soroban, Stellar's smart contract platform.
//!
//! MiMC Implementation:
//!   - x^7 exponent, 91 rounds, Miyaguchi-Preneel compression
//!   - keccak256 hash chain from seed "mimc" for round constants (precomputed)
//!   - 29 level-specific IVs for Merkle tree
//!   - Full node storage using Soroban persistent storage
//!
//! Proof verification:
//!   - Oracle-based (Soroban lacks BN254 pairing precompiles)
//!   - Trusted oracle submits attestation via submit_attestation()
//!
//! Supported: XLM (native)
//!
//! Copyright 2024 Miximus Authors — GPL-3.0-or-later

#![no_std]

use soroban_sdk::{
    contract, contractimpl, contracttype, Address, BytesN, Env, Symbol,
    log,
};

pub const TREE_DEPTH: u32 = 29;
pub const MAX_LEAVES: u64 = 1 << TREE_DEPTH;
pub const MIMC_ROUNDS: u32 = 91;

// BN254 scalar field modulus stored as 4 x u64 limbs (little-endian)
const SCALAR_FIELD_LIMBS: [u64; 4] = [
    0x43e1f593f0000001,
    0x2833e84879b97091,
    0xb85045b68181585d,
    0x30644e72e131a029,
];

// =========================================================================
//                      256-BIT FIELD ARITHMETIC
// =========================================================================

/// 256-bit unsigned integer as 4 x u64 limbs (little-endian limb order)
#[derive(Clone, Copy)]
struct U256([u64; 4]);

impl U256 {
    fn zero() -> Self { U256([0u64; 4]) }

    fn from_be_bytes(bytes: &[u8; 32]) -> Self {
        let mut limbs = [0u64; 4];
        for i in 0..4 {
            let off = 24 - i * 8;
            let mut v = 0u64;
            for j in 0..8 { v = (v << 8) | (bytes[off + j] as u64); }
            limbs[i] = v;
        }
        U256(limbs)
    }

    fn to_be_bytes(&self) -> [u8; 32] {
        let mut bytes = [0u8; 32];
        for i in 0..4 {
            let off = 24 - i * 8;
            let v = self.0[i];
            for j in 0..8 { bytes[off + j] = ((v >> (56 - j * 8)) & 0xff) as u8; }
        }
        bytes
    }

    fn add_carry(a: &U256, b: &U256) -> (U256, bool) {
        let mut r = [0u64; 4];
        let mut c = 0u128;
        for i in 0..4 {
            c += a.0[i] as u128 + b.0[i] as u128;
            r[i] = c as u64;
            c >>= 64;
        }
        (U256(r), c != 0)
    }

    fn sub_borrow(a: &U256, b: &U256) -> (U256, bool) {
        let mut r = [0u64; 4];
        let mut borrow = 0i128;
        for i in 0..4 {
            borrow += a.0[i] as i128 - b.0[i] as i128;
            r[i] = borrow as u64;
            borrow >>= 64;
        }
        (U256(r), borrow < 0)
    }

    fn gte(a: &U256, b: &U256) -> bool {
        for i in (0..4).rev() {
            if a.0[i] > b.0[i] { return true; }
            if a.0[i] < b.0[i] { return false; }
        }
        true
    }
}

fn addmod(a: &U256, b: &U256, p: &U256) -> U256 {
    let (s, c) = U256::add_carry(a, b);
    if c || U256::gte(&s, p) {
        let (r, _) = U256::sub_borrow(&s, p);
        r
    } else { s }
}

fn mulmod(a: &U256, b: &U256, p: &U256) -> U256 {
    let mut prod = [0u64; 8];
    for i in 0..4 {
        let mut carry = 0u128;
        for j in 0..4 {
            let v = prod[i + j] as u128 + (a.0[i] as u128) * (b.0[j] as u128) + carry;
            prod[i + j] = v as u64;
            carry = v >> 64;
        }
        prod[i + 4] = carry as u64;
    }
    mod_reduce_512(&prod, p)
}

fn mod_reduce_512(val: &[u64; 8], p: &U256) -> U256 {
    let mut r = U256::zero();
    for i in (0..8).rev() {
        for bit in (0..64).rev() {
            let mut carry = 0u64;
            for j in 0..4 {
                let nc = r.0[j] >> 63;
                r.0[j] = (r.0[j] << 1) | carry;
                carry = nc;
            }
            let b = (val[i] >> bit) & 1;
            if b != 0 {
                let (nr, c) = U256::add_carry(&r, &U256([1, 0, 0, 0]));
                r = nr;
                if c || U256::gte(&r, p) {
                    let (s, _) = U256::sub_borrow(&r, p);
                    r = s;
                }
            } else if U256::gte(&r, p) {
                let (s, _) = U256::sub_borrow(&r, p);
                r = s;
            }
        }
    }
    r
}

// =========================================================================
//                         MiMC IMPLEMENTATION
// =========================================================================

/// Decimal string to 32-byte big-endian
fn dec_to_bytes(s: &[u8]) -> [u8; 32] {
    let mut val = [0u64; 4];
    for &ch in s {
        let digit = (ch - b'0') as u128;
        let mut carry = digit;
        for limb in val.iter_mut() {
            let v = (*limb as u128) * 10 + carry;
            *limb = v as u64;
            carry = v >> 64;
        }
    }
    U256(val).to_be_bytes()
}

/// Get round constant i (0..91)
fn get_round_constant(i: u32) -> [u8; 32] {
    // All 91 round constants precomputed from keccak256 hash chain
    const RC: [&[u8]; 91] = [
        b"9699427722198585233576395554477836603696224056248062887534150762780491344964",
        b"11703485025028567684989973226085996971982211366514589794869047827993715158284",
        b"16047385151842759715883983147732529094829228988006114315106338214348641493684",
        b"13171044560831470721204611089017807586748478995617618605757094330776784097979",
        b"463481810611863887895788181329300079259271913906328008157226405515633707060",
        b"14172737021216375674608750505647811061638328766015439391923848653810108862588",
        b"6689253641270970867338559588710848917420486594299189953566661581223880803412",
        b"6206378175987060350257013170941207256607267189110167715983507598036299759965",
        b"1868042604362664669096366350611088510094968563432118553423582843551251304148",
        b"3800923262676983849094741417247145368534214456118022255739022670427323747241",
        b"21591653578493131795224521299603914344271257669274375926196191948855055965941",
        b"10138810537922542300776837825791273739833273537236869643130335662561281936350",
        b"21574990455760257279296102927467279097968749263922051042846339699523743272465",
        b"16413121409077715441301059134455418701149785095704101665410282589314114365979",
        b"18250165490760061617105180803396666700674782964557583105320693987373016905441",
        b"7502779237586675485986299191768705581745728775671111833683511364027159171547",
        b"1871191249878415346013267028522443901105779688422863746611768655449989698507",
        b"14227980513379364932114804248442005973014852536227916890481139769683689826355",
        b"3626911537588022011409641665074817121756047123479165039814180423250987306580",
        b"19236074515568966224364617729593024174260343399978065715191519989928891482976",
        b"18303998739805578246875337832148027492674021151790013986107100904482029912855",
        b"15029356798333672110948390526097772289805005615627335370974040111484189851218",
        b"14009969076553308167172322716790329101547548435494434267861550029341368702955",
        b"2474399186054189702290953445489943933900186003481592958790627091252800758972",
        b"8499363137467817080120995943388159435886438129064869562976936976416160626765",
        b"3721538106651623159107419551085379332003626724680311764467196000779836528731",
        b"21513636789136435447726989659244632115907105013743178557543258425580816693013",
        b"6413499256104003900741626312911121949489199328341000172772535477686526161933",
        b"2423296695146958228105381999662588996417033334902395826324000730015059834867",
        b"14226452914890638176054896095327353311080202567202372321925082986206459137544",
        b"6668382834823585601183694406027162564759781576975604144599361907050393232654",
        b"7684682799902615328244940431427150087576264917860561004999996369763189716339",
        b"1889098908550857440616721504788014180820394851645231730287772458817397711248",
        b"6790625100354137563247974716700975825598182679172705081021265590776550026003",
        b"14773642371467989182995352422864409987810184892360173574623635121892742318878",
        b"19281448673732014642910881470629992531175666415068593568792064770752528727527",
        b"14533954802572082864290492673227299700287092879109981683817768414021039892181",
        b"7201323559292680367910220192893057999794593519219870913061645135556363761573",
        b"6732093332172534276604522937404905062834700997517855193752580368599291894652",
        b"17933540691001452559591212829318968824204486615606043739961347674721175964688",
        b"15961428780882917777414183392499617830167889800177298058040067254864354220287",
        b"12736214278132568876546350800822513740641931888358727849082359697485160982736",
        b"3439545814879193145334860319308882824567292287099085145529516573177554898",
        b"4304870388935813588332366794108449982123835995998847432495865244184755242106",
        b"18271176884720092981015377059918454136111894884653348162306733411625184098874",
        b"251341252390741357756739920423555089029964239608738693075482944570024594299",
        b"7061267873969201870294342652138581026512927130814986082309102676881598814324",
        b"15210185781629509117331823557188083554772921877973145839484228940930659831750",
        b"9933623231487467132083483273870403237369290780152447195366060181388225747404",
        b"8860207495959673050021299042484291804204364210189770038730065043316249584034",
        b"8225607920290235351257457224426373001131595237198233026729554520653645104823",
        b"2101754597405698707301278803774189375304825984406927629163551182822992849211",
        b"4650809359262437639973871683963300301321123052952582481016111916526927963510",
        b"2819469806498716032331303763953858334192180747654195125067222852780007249613",
        b"10026181953811808826365146991560498259739127762700251538466935087699710718980",
        b"4068800227252222261356221780345265002310350839287995811420025897830262605550",
        b"7706556989153408298246769455370263501638954772224719089725449880345119864895",
        b"708143970965367424687385234288223247694427964053921277910837987862864278471",
        b"14675466731217481032178475947165924106635215526640697173147623987334826158887",
        b"2891548451588016327005422884294243001284598433952314748085541373140885524236",
        b"3248061135531730385352170229977825871322045066439582053613486809232947427425",
        b"20009604326387202734077903479052788729780477058651868498203471330807320243485",
        b"16777657208000185795670509937485592891624105910517450105614416248715035393568",
        b"4651836398927038829184868494635901984396480816917764202384582304105185756554",
        b"18751163994760169650397520229993366266478887832036941208422557666277977396759",
        b"12897721113527742861792389851089500547852915763547646899857970659940475514927",
        b"8809619201418684241029036556295591884232522813567928806176674235810410775604",
        b"12764568073160656986674789706181758338655490354081965460240045247683040081962",
        b"21502007337926341717114099094861709208431032111194678565440998870068188932610",
        b"6676554273606654034460232727824636863338632772826173222585689559169300842540",
        b"3138170934188033588407671000185359515289243280807075679810358484377717004344",
        b"5016504702993786669228778886709524960531243371932953717103586353783767283841",
        b"3641096259839778412296729683448541948339993242606085025349116868466429331109",
        b"17482178485290445442249591236781385361832252325559581596476967807317491695738",
        b"17159462194092251514229072648808575169874022757757552441138883401008323177315",
        b"7191903234268516892114204272287340227826681638192854529199275252092439950293",
        b"5945747129617066655054359784112681539348647904456722905528854333831147439943",
        b"11682653935985309726471808915274638394951372080323090060070436784000986335305",
        b"2116213598349300952598605376561162484274388090426753376198347878848540790895",
        b"5714326248919187415740532589098943107423637397599181819843406048950342329379",
        b"13894119751705485508983929457149987156694369489992252755933362006151149676448",
        b"10319593038266123453300247039462513707023223679302391278432798959473214716610",
        b"1128983626080142661579089137513406106577305284945391649710553073832876332136",
        b"4248221674033135716761210686080451495544280437155108649667019402496077376836",
        b"838734091064411908005800793077104281843536168985419652740371543899822735427",
        b"5199375564065532653333317325418032515582457298266061759973576494056772335768",
        b"15300100374635143049391673582783434554769070281785839589894321842312801791719",
        b"1529479817569769913729209110401024980435414116932327874985316118115320812957",
        b"15270665240183241039904197262371028528545133272760122628694554835599635383702",
        b"5641557314750776584122438294951634757985170942845644455628527989761038140088",
        b"16326288709402544922431865006266288658569438060902755495235802091617779198057",
    ];
    dec_to_bytes(RC[i as usize])
}

/// Get level IV for Merkle tree level (0..28)
fn get_level_iv(level: u32) -> [u8; 32] {
    const IVS: [&[u8]; 29] = [
        b"149674538925118052205057075966660054952481571156186698930522557832224430770",
        b"9670701465464311903249220692483401938888498641874948577387207195814981706974",
        b"18318710344500308168304415114839554107298291987930233567781901093928276468271",
        b"6597209388525824933845812104623007130464197923269180086306970975123437805179",
        b"21720956803147356712695575768577036859892220417043839172295094119877855004262",
        b"10330261616520855230513677034606076056972336573153777401182178891807369896722",
        b"17466547730316258748333298168566143799241073466140136663575045164199607937939",
        b"18881017304615283094648494495339883533502299318365959655029893746755475886610",
        b"21580915712563378725413940003372103925756594604076607277692074507345076595494",
        b"12316305934357579015754723412431647910012873427291630993042374701002287130550",
        b"18905410889238873726515380969411495891004493295170115920825550288019118582494",
        b"12819107342879320352602391015489840916114959026915005817918724958237245903353",
        b"8245796392944118634696709403074300923517437202166861682117022548371601758802",
        b"16953062784314687781686527153155644849196472783922227794465158787843281909585",
        b"19346880451250915556764413197424554385509847473349107460608536657852472800734",
        b"14486794857958402714787584825989957493343996287314210390323617462452254101347",
        b"11127491343750635061768291849689189917973916562037173191089384809465548650641",
        b"12217916643258751952878742936579902345100885664187835381214622522318889050675",
        b"722025110834410790007814375535296040832778338853544117497481480537806506496",
        b"15115624438829798766134408951193645901537753720219896384705782209102859383951",
        b"11495230981884427516908372448237146604382590904456048258839160861769955046544",
        b"16867999085723044773810250829569850875786210932876177117428755424200948460050",
        b"1884116508014449609846749684134533293456072152192763829918284704109129550542",
        b"14643335163846663204197941112945447472862168442334003800621296569318670799451",
        b"1933387276732345916104540506251808516402995586485132246682941535467305930334",
        b"7286414555941977227951257572976885370489143210539802284740420664558593616067",
        b"16932161189449419608528042274282099409408565503929504242784173714823499212410",
        b"16562533130736679030886586765487416082772837813468081467237161865787494093536",
        b"6037428193077828806710267464232314380014232668931818917272972397574634037180",
    ];
    dec_to_bytes(IVS[level as usize])
}

/// MiMC cipher: E_k(x) with x^7 exponent and 91 rounds
fn mimc_cipher(in_x: &[u8; 32], in_k: &[u8; 32]) -> [u8; 32] {
    let p = U256(SCALAR_FIELD_LIMBS);
    let mut x = U256::from_be_bytes(in_x);
    let k = U256::from_be_bytes(in_k);

    for i in 0..MIMC_ROUNDS {
        let c = U256::from_be_bytes(&get_round_constant(i));
        let t = addmod(&addmod(&x, &c, &p), &k, &p);
        let t2 = mulmod(&t, &t, &p);
        let t4 = mulmod(&t2, &t2, &p);
        let t6 = mulmod(&t4, &t2, &p);
        x = mulmod(&t6, &t, &p);
    }
    addmod(&x, &k, &p).to_be_bytes()
}

/// MiMC hash with Miyaguchi-Preneel compression and custom IV
fn mimc_hash_with_iv(data: &[[u8; 32]], iv: &[u8; 32]) -> [u8; 32] {
    let p = U256(SCALAR_FIELD_LIMBS);
    let mut r = U256::from_be_bytes(iv);

    for item in data {
        let x = U256::from_be_bytes(item);
        let r_bytes = r.to_be_bytes();
        let h = U256::from_be_bytes(&mimc_cipher(item, &r_bytes));
        r = addmod(&addmod(&r, &x, &p), &h, &p);
    }
    r.to_be_bytes()
}

/// MiMC hash with IV=0
fn mimc_hash(data: &[[u8; 32]]) -> [u8; 32] {
    mimc_hash_with_iv(data, &[0u8; 32])
}

/// Hash two children at a given Merkle tree level
fn merkle_hash(level: u32, left: &[u8; 32], right: &[u8; 32]) -> [u8; 32] {
    let iv = get_level_iv(level);
    mimc_hash_with_iv(&[*left, *right], &iv)
}

// =========================================================================
//                       CONTRACT DATA KEYS
// =========================================================================

#[contracttype]
#[derive(Clone)]
pub enum DataKey {
    Denomination,
    AssetSymbol,
    Owner,
    Oracle,
    NextLeafIndex,
    CurrentRoot,
    Nullifier(BytesN<32>),
    Root(BytesN<32>),
    /// Tree node storage: (level, index) -> hash
    TreeNode(u32, u64),
    /// Zero hash for each level
    ZeroHash(u32),
    /// Oracle attestation for proof verification
    Attestation(BytesN<32>),
    VkData,
}

#[contract]
pub struct MiximusStellar;

#[contractimpl]
impl MiximusStellar {
    /// Initialize the mixer contract
    pub fn initialize(
        env: Env,
        denomination: i128,
        asset_symbol: Symbol,
        oracle: Address,
        vk_data: BytesN<32>,
    ) {
        if env.storage().instance().has(&DataKey::Owner) {
            panic!("Already initialized");
        }

        env.storage().instance().set(&DataKey::Denomination, &denomination);
        env.storage().instance().set(&DataKey::AssetSymbol, &asset_symbol);
        env.storage().instance().set(&DataKey::Owner, &env.current_contract_address());
        env.storage().instance().set(&DataKey::Oracle, &oracle);
        env.storage().instance().set(&DataKey::NextLeafIndex, &0u64);
        env.storage().instance().set(&DataKey::VkData, &vk_data);

        // Initialize Merkle tree with MiMC zero hashes
        let mut zero = [0u8; 32];
        for i in 0..TREE_DEPTH {
            let zero_bn = BytesN::from_array(&env, &zero);
            env.storage().persistent().set(&DataKey::ZeroHash(i), &zero_bn);
            zero = merkle_hash(i, &zero, &zero);
        }

        let root = BytesN::from_array(&env, &zero);
        env.storage().instance().set(&DataKey::CurrentRoot, &root);
        env.storage().persistent().set(&DataKey::Root(root.clone()), &true);

        log!(&env, "Miximus initialized: denomination={}", denomination);
    }

    /// Deposit XLM into the mixer
    pub fn deposit(
        env: Env,
        depositor: Address,
        leaf_hash: BytesN<32>,
    ) -> (BytesN<32>, u64) {
        depositor.require_auth();

        let denomination: i128 = env.storage().instance().get(&DataKey::Denomination).unwrap();
        let next_index: u64 = env.storage().instance().get(&DataKey::NextLeafIndex).unwrap();

        if next_index >= MAX_LEAVES {
            panic!("Merkle tree is full");
        }

        // Transfer XLM from depositor to contract
        let contract = env.current_contract_address();
        soroban_sdk::token::Client::new(&env, &contract)
            .transfer(&depositor, &contract, &denomination);

        // Insert leaf into full-node MiMC Merkle tree
        let leaf_bytes = Self::bytes_to_array(&env, &leaf_hash);
        let new_root_bytes = Self::insert_leaf(&env, &leaf_bytes, next_index);
        let new_root = BytesN::from_array(&env, &new_root_bytes);

        env.storage().instance().set(&DataKey::CurrentRoot, &new_root);
        env.storage().instance().set(&DataKey::NextLeafIndex, &(next_index + 1));
        env.storage().persistent().set(&DataKey::Root(new_root.clone()), &true);

        log!(&env, "Deposit: index={}", next_index);

        (new_root, next_index)
    }

    /// Batch deposit XLM — deposit N units in a single transaction
    pub fn batch_deposit(
        env: Env,
        depositor: Address,
        leaf_hashes: Vec<BytesN<32>>,
    ) -> Vec<(BytesN<32>, u64)> {
        depositor.require_auth();

        let count = leaf_hashes.len() as u32;
        if count == 0 || count > 20 {
            panic!("Batch size must be 1-20");
        }

        let denomination: i128 = env.storage().instance().get(&DataKey::Denomination).unwrap();
        let total_amount = denomination * count as i128;

        // Transfer total XLM from depositor to contract
        let contract = env.current_contract_address();
        soroban_sdk::token::Client::new(&env, &contract)
            .transfer(&depositor, &contract, &total_amount);

        let mut next_index: u64 = env.storage().instance().get(&DataKey::NextLeafIndex).unwrap();
        let mut results = Vec::new(&env);

        for leaf_hash in leaf_hashes.iter() {
            if next_index >= MAX_LEAVES {
                panic!("Merkle tree is full");
            }

            let leaf_bytes = Self::bytes_to_array(&env, &leaf_hash);
            let new_root_bytes = Self::insert_leaf(&env, &leaf_bytes, next_index);
            let new_root = BytesN::from_array(&env, &new_root_bytes);

            env.storage().instance().set(&DataKey::CurrentRoot, &new_root);
            env.storage().persistent().set(&DataKey::Root(new_root.clone()), &true);

            results.push_back((new_root.clone(), next_index));
            next_index += 1;
        }

        env.storage().instance().set(&DataKey::NextLeafIndex, &next_index);
        log!(&env, "Batch deposit: count={}", count);

        results
    }

    /// Submit proof attestation (oracle only)
    pub fn submit_attestation(
        env: Env,
        oracle: Address,
        pub_hash: BytesN<32>,
    ) {
        oracle.require_auth();
        let stored_oracle: Address = env.storage().instance().get(&DataKey::Oracle).unwrap();
        if oracle != stored_oracle {
            panic!("Not authorized oracle");
        }
        env.storage().persistent().set(&DataKey::Attestation(pub_hash), &true);
    }

    /// Withdraw XLM using zkSNARK proof (oracle-verified)
    pub fn withdraw(
        env: Env,
        root: BytesN<32>,
        nullifier: BytesN<32>,
        pub_hash: BytesN<32>,
        recipient: Address,
    ) {
        // Check nullifier
        if env.storage().persistent().get(&DataKey::Nullifier(nullifier.clone())).unwrap_or(false) {
            panic!("Double-spend");
        }

        // Check root
        if !env.storage().persistent().get(&DataKey::Root(root.clone())).unwrap_or(false) {
            panic!("Unknown root");
        }

        // Verify oracle attestation
        if !env.storage().persistent().get(&DataKey::Attestation(pub_hash.clone())).unwrap_or(false) {
            panic!("No oracle attestation for this proof");
        }

        // Mark nullifier as spent
        env.storage().persistent().set(&DataKey::Nullifier(nullifier), &true);
        // Remove attestation
        env.storage().persistent().set(&DataKey::Attestation(pub_hash), &false);

        // Transfer to recipient
        let denomination: i128 = env.storage().instance().get(&DataKey::Denomination).unwrap();
        let contract = env.current_contract_address();
        soroban_sdk::token::Client::new(&env, &contract)
            .transfer(&contract, &recipient, &denomination);

        log!(&env, "Withdrawal to {}", recipient);
    }

    /// Batch withdraw XLM — process up to 5 withdrawals in a single transaction
    pub fn batch_withdraw(
        env: Env,
        roots: Vec<BytesN<32>>,
        nullifiers: Vec<BytesN<32>>,
        pub_hashes: Vec<BytesN<32>>,
        recipient: Address,
    ) {
        let count = roots.len() as u32;
        if count == 0 || count > 5 {
            panic!("Batch size must be 1-5");
        }
        if nullifiers.len() != roots.len() || pub_hashes.len() != roots.len() {
            panic!("Array lengths must match");
        }

        for i in 0..count {
            let nullifier = nullifiers.get(i).unwrap();
            let root = roots.get(i).unwrap();
            let pub_hash = pub_hashes.get(i).unwrap();

            // Check nullifier
            if env.storage().persistent().get(&DataKey::Nullifier(nullifier.clone())).unwrap_or(false) {
                panic!("Double-spend");
            }
            // Check root
            if !env.storage().persistent().get(&DataKey::Root(root.clone())).unwrap_or(false) {
                panic!("Unknown root");
            }
            // Verify oracle attestation
            if !env.storage().persistent().get(&DataKey::Attestation(pub_hash.clone())).unwrap_or(false) {
                panic!("No oracle attestation for this proof");
            }

            // Mark nullifier as spent and remove attestation
            env.storage().persistent().set(&DataKey::Nullifier(nullifier), &true);
            env.storage().persistent().set(&DataKey::Attestation(pub_hash), &false);
        }

        // Transfer total to recipient
        let denomination: i128 = env.storage().instance().get(&DataKey::Denomination).unwrap();
        let total = denomination * count as i128;
        let contract = env.current_contract_address();
        soroban_sdk::token::Client::new(&env, &contract)
            .transfer(&contract, &recipient, &total);

        log!(&env, "Batch withdrawal: count={} to {}", count, recipient);
    }

    /// Set oracle address (owner only)
    pub fn set_oracle(env: Env, caller: Address, new_oracle: Address) {
        caller.require_auth();
        let owner: Address = env.storage().instance().get(&DataKey::Owner).unwrap();
        if caller != owner {
            panic!("Not owner");
        }
        env.storage().instance().set(&DataKey::Oracle, &new_oracle);
    }

    // View methods
    pub fn get_root(env: Env) -> BytesN<32> {
        env.storage().instance().get(&DataKey::CurrentRoot).unwrap()
    }

    pub fn is_spent(env: Env, nullifier: BytesN<32>) -> bool {
        env.storage().persistent().get(&DataKey::Nullifier(nullifier)).unwrap_or(false)
    }

    pub fn get_denomination(env: Env) -> i128 {
        env.storage().instance().get(&DataKey::Denomination).unwrap()
    }

    pub fn get_oracle(env: Env) -> Address {
        env.storage().instance().get(&DataKey::Oracle).unwrap()
    }

    // Internal helpers

    fn bytes_to_array(env: &Env, bn: &BytesN<32>) -> [u8; 32] {
        let mut arr = [0u8; 32];
        bn.copy_into_slice(&mut arr);
        arr
    }

    fn get_node(env: &Env, level: u32, index: u64) -> [u8; 32] {
        let key = DataKey::TreeNode(level, index);
        match env.storage().persistent().get::<DataKey, BytesN<32>>(&key) {
            Some(bn) => Self::bytes_to_array(env, &bn),
            None => {
                // Return zero hash for this level
                match env.storage().persistent().get::<DataKey, BytesN<32>>(&DataKey::ZeroHash(level)) {
                    Some(bn) => Self::bytes_to_array(env, &bn),
                    None => [0u8; 32],
                }
            }
        }
    }

    fn insert_leaf(env: &Env, leaf: &[u8; 32], index: u64) -> [u8; 32] {
        // Store leaf at level 0
        let leaf_bn = BytesN::from_array(env, leaf);
        env.storage().persistent().set(&DataKey::TreeNode(0, index), &leaf_bn);

        let mut current = *leaf;
        let mut idx = index;

        for level in 0..TREE_DEPTH {
            let parent_idx = idx / 2;
            let (left, right) = if idx % 2 == 0 {
                (current, Self::get_node(env, level, idx + 1))
            } else {
                (Self::get_node(env, level, idx - 1), current)
            };

            current = merkle_hash(level, &left, &right);

            // Store parent node
            let parent_bn = BytesN::from_array(env, &current);
            env.storage().persistent().set(&DataKey::TreeNode(level + 1, parent_idx), &parent_bn);

            idx = parent_idx;
        }

        current
    }
}
