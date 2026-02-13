//! Miximus Polkadot Smart Contract (ink!)
//!
//! zkSNARK-based mixer for Polkadot (DOT).
//! Written in ink! for deployment on Substrate chains with the Contracts pallet.
//!
//! MiMC Implementation:
//!   - x^7 exponent, 91 rounds, Miyaguchi-Preneel compression
//!   - keccak256 hash chain from seed "mimc" for round constants (precomputed)
//!   - 29 level-specific IVs for Merkle tree
//!   - Full node storage using ink! Mapping
//!
//! Proof verification:
//!   - Oracle-based (Substrate has crypto host functions but no BN254 pairing)
//!   - Trusted oracle submits attestation that is checked on withdraw
//!
//! Supported: DOT (native), any Substrate chain with Contracts pallet
//!
//! Copyright 2024 Miximus Authors — GPL-3.0-or-later

#![cfg_attr(not(feature = "std"), no_std, no_main)]

#[ink::contract]
mod miximus {
    use ink::prelude::vec::Vec;
    use ink::storage::Mapping;

    pub const TREE_DEPTH: usize = 29;
    pub const MAX_LEAVES: u64 = 1 << TREE_DEPTH;

    /// BN254 scalar field modulus
    /// 21888242871839275222246405745257275088548364400416034343698204186575808495617
    /// Stored as 4 x u64 limbs (little-endian): [low, ..., high]
    const SCALAR_FIELD: [u64; 4] = [
        0x43e1f593f0000001,
        0x2833e84879b97091,
        0xb85045b68181585d,
        0x30644e72e131a029,
    ];

    /// MiMC round constants count
    const MIMC_ROUNDS: usize = 91;

    // =========================================================================
    //                      256-BIT FIELD ARITHMETIC
    // =========================================================================
    // We represent field elements as [u8; 32] in big-endian format.
    // For arithmetic, we convert to/from a simple big-integer representation.

    /// A 256-bit unsigned integer stored as 4 x u64 limbs (little-endian limb order)
    #[derive(Clone, Copy, Default)]
    struct U256([u64; 4]);

    impl U256 {
        fn zero() -> Self {
            U256([0u64; 4])
        }

        fn from_be_bytes(bytes: &[u8; 32]) -> Self {
            let mut limbs = [0u64; 4];
            for i in 0..4 {
                let offset = 24 - i * 8;
                let mut val = 0u64;
                for j in 0..8 {
                    val = (val << 8) | (bytes[offset + j] as u64);
                }
                limbs[i] = val;
            }
            U256(limbs)
        }

        fn to_be_bytes(&self) -> [u8; 32] {
            let mut bytes = [0u8; 32];
            for i in 0..4 {
                let offset = 24 - i * 8;
                let val = self.0[i];
                for j in 0..8 {
                    bytes[offset + j] = ((val >> (56 - j * 8)) & 0xff) as u8;
                }
            }
            bytes
        }

        /// Add two U256 values, returning (result, carry)
        fn add_with_carry(a: &U256, b: &U256) -> (U256, bool) {
            let mut result = [0u64; 4];
            let mut carry = 0u128;
            for i in 0..4 {
                carry += a.0[i] as u128 + b.0[i] as u128;
                result[i] = carry as u64;
                carry >>= 64;
            }
            (U256(result), carry != 0)
        }

        /// Subtract b from a, returning (result, borrow)
        fn sub_with_borrow(a: &U256, b: &U256) -> (U256, bool) {
            let mut result = [0u64; 4];
            let mut borrow = 0i128;
            for i in 0..4 {
                borrow += a.0[i] as i128 - b.0[i] as i128;
                result[i] = borrow as u64;
                borrow >>= 64;
            }
            (U256(result), borrow < 0)
        }

        /// Check if a >= b
        fn gte(a: &U256, b: &U256) -> bool {
            for i in (0..4).rev() {
                if a.0[i] > b.0[i] { return true; }
                if a.0[i] < b.0[i] { return false; }
            }
            true // equal
        }
    }

    /// Modular addition: (a + b) mod p
    fn addmod(a: &U256, b: &U256, p: &U256) -> U256 {
        let (sum, carry) = U256::add_with_carry(a, b);
        if carry || U256::gte(&sum, p) {
            let (result, _) = U256::sub_with_borrow(&sum, p);
            result
        } else {
            sum
        }
    }

    /// Modular multiplication: (a * b) mod p using schoolbook method with 512-bit intermediate
    fn mulmod(a: &U256, b: &U256, p: &U256) -> U256 {
        // 512-bit product
        let mut product = [0u64; 8];
        for i in 0..4 {
            let mut carry = 0u128;
            for j in 0..4 {
                let val = product[i + j] as u128
                    + (a.0[i] as u128) * (b.0[j] as u128)
                    + carry;
                product[i + j] = val as u64;
                carry = val >> 64;
            }
            product[i + 4] = carry as u64;
        }
        // Barrett-like reduction: divide product by p
        // Simple approach: repeated subtraction for correctness (production: use Montgomery)
        mod_reduce_512(&product, p)
    }

    /// Reduce a 512-bit number mod p
    fn mod_reduce_512(val: &[u64; 8], p: &U256) -> U256 {
        // Shift-and-subtract reduction
        // We process from the top bit down
        let mut result = [0u64; 4];
        result.copy_from_slice(&val[..4]);
        let mut remainder = U256(result);

        // Handle the upper 256 bits
        for i in (4..8).rev() {
            for bit in (0..64).rev() {
                // Shift remainder left by 1
                let mut carry = 0u64;
                for j in 0..4 {
                    let new_carry = remainder.0[j] >> 63;
                    remainder.0[j] = (remainder.0[j] << 1) | carry;
                    carry = new_carry;
                }
                // Add the current bit
                let current_bit = (val[i] >> bit) & 1;
                let (new_rem, c) = U256::add_with_carry(
                    &remainder,
                    &U256([current_bit, 0, 0, 0]),
                );
                remainder = new_rem;
                // Subtract p if >= p
                if c || U256::gte(&remainder, p) {
                    let (sub, _) = U256::sub_with_borrow(&remainder, p);
                    remainder = sub;
                }
            }
        }

        // Now process the lower 256 bits (already in val[0..4])
        // We need to continue the shift-reduce from the upper part
        // Actually, let's redo this properly:
        // Start fresh with a 512->256 reduction
        let mut r = U256::zero();
        for i in (0..8).rev() {
            for bit in (0..64).rev() {
                // Shift r left by 1
                let mut carry = 0u64;
                for j in 0..4 {
                    let new_carry = r.0[j] >> 63;
                    r.0[j] = (r.0[j] << 1) | carry;
                    carry = new_carry;
                }
                // Add bit
                let current_bit = (val[i] >> bit) & 1;
                if current_bit != 0 {
                    let (new_r, c) = U256::add_with_carry(&r, &U256([1, 0, 0, 0]));
                    r = new_r;
                    if c || U256::gte(&r, p) {
                        let (sub, _) = U256::sub_with_borrow(&r, p);
                        r = sub;
                    }
                } else if U256::gte(&r, p) {
                    let (sub, _) = U256::sub_with_borrow(&r, p);
                    r = sub;
                }
            }
        }
        r
    }

    // =========================================================================
    //                         MiMC IMPLEMENTATION
    // =========================================================================

    /// 91 MiMC round constants (precomputed from keccak256 hash chain with seed "mimc")
    fn mimc_round_constants() -> [[u8; 32]; MIMC_ROUNDS] {
        let constants_decimal: [&str; 91] = [
            "9699427722198585233576395554477836603696224056248062887534150762780491344964",
            "11703485025028567684989973226085996971982211366514589794869047827993715158284",
            "16047385151842759715883983147732529094829228988006114315106338214348641493684",
            "13171044560831470721204611089017807586748478995617618605757094330776784097979",
            "463481810611863887895788181329300079259271913906328008157226405515633707060",
            "14172737021216375674608750505647811061638328766015439391923848653810108862588",
            "6689253641270970867338559588710848917420486594299189953566661581223880803412",
            "6206378175987060350257013170941207256607267189110167715983507598036299759965",
            "1868042604362664669096366350611088510094968563432118553423582843551251304148",
            "3800923262676983849094741417247145368534214456118022255739022670427323747241",
            "21591653578493131795224521299603914344271257669274375926196191948855055965941",
            "10138810537922542300776837825791273739833273537236869643130335662561281936350",
            "21574990455760257279296102927467279097968749263922051042846339699523743272465",
            "16413121409077715441301059134455418701149785095704101665410282589314114365979",
            "18250165490760061617105180803396666700674782964557583105320693987373016905441",
            "7502779237586675485986299191768705581745728775671111833683511364027159171547",
            "1871191249878415346013267028522443901105779688422863746611768655449989698507",
            "14227980513379364932114804248442005973014852536227916890481139769683689826355",
            "3626911537588022011409641665074817121756047123479165039814180423250987306580",
            "19236074515568966224364617729593024174260343399978065715191519989928891482976",
            "18303998739805578246875337832148027492674021151790013986107100904482029912855",
            "15029356798333672110948390526097772289805005615627335370974040111484189851218",
            "14009969076553308167172322716790329101547548435494434267861550029341368702955",
            "2474399186054189702290953445489943933900186003481592958790627091252800758972",
            "8499363137467817080120995943388159435886438129064869562976936976416160626765",
            "3721538106651623159107419551085379332003626724680311764467196000779836528731",
            "21513636789136435447726989659244632115907105013743178557543258425580816693013",
            "6413499256104003900741626312911121949489199328341000172772535477686526161933",
            "2423296695146958228105381999662588996417033334902395826324000730015059834867",
            "14226452914890638176054896095327353311080202567202372321925082986206459137544",
            "6668382834823585601183694406027162564759781576975604144599361907050393232654",
            "7684682799902615328244940431427150087576264917860561004999996369763189716339",
            "1889098908550857440616721504788014180820394851645231730287772458817397711248",
            "6790625100354137563247974716700975825598182679172705081021265590776550026003",
            "14773642371467989182995352422864409987810184892360173574623635121892742318878",
            "19281448673732014642910881470629992531175666415068593568792064770752528727527",
            "14533954802572082864290492673227299700287092879109981683817768414021039892181",
            "7201323559292680367910220192893057999794593519219870913061645135556363761573",
            "6732093332172534276604522937404905062834700997517855193752580368599291894652",
            "17933540691001452559591212829318968824204486615606043739961347674721175964688",
            "15961428780882917777414183392499617830167889800177298058040067254864354220287",
            "12736214278132568876546350800822513740641931888358727849082359697485160982736",
            "3439545814879193145334860319308882824567292287099085145529516573177554898",
            "4304870388935813588332366794108449982123835995998847432495865244184755242106",
            "18271176884720092981015377059918454136111894884653348162306733411625184098874",
            "251341252390741357756739920423555089029964239608738693075482944570024594299",
            "7061267873969201870294342652138581026512927130814986082309102676881598814324",
            "15210185781629509117331823557188083554772921877973145839484228940930659831750",
            "9933623231487467132083483273870403237369290780152447195366060181388225747404",
            "8860207495959673050021299042484291804204364210189770038730065043316249584034",
            "8225607920290235351257457224426373001131595237198233026729554520653645104823",
            "2101754597405698707301278803774189375304825984406927629163551182822992849211",
            "4650809359262437639973871683963300301321123052952582481016111916526927963510",
            "2819469806498716032331303763953858334192180747654195125067222852780007249613",
            "10026181953811808826365146991560498259739127762700251538466935087699710718980",
            "4068800227252222261356221780345265002310350839287995811420025897830262605550",
            "7706556989153408298246769455370263501638954772224719089725449880345119864895",
            "708143970965367424687385234288223247694427964053921277910837987862864278471",
            "14675466731217481032178475947165924106635215526640697173147623987334826158887",
            "2891548451588016327005422884294243001284598433952314748085541373140885524236",
            "3248061135531730385352170229977825871322045066439582053613486809232947427425",
            "20009604326387202734077903479052788729780477058651868498203471330807320243485",
            "16777657208000185795670509937485592891624105910517450105614416248715035393568",
            "4651836398927038829184868494635901984396480816917764202384582304105185756554",
            "18751163994760169650397520229993366266478887832036941208422557666277977396759",
            "12897721113527742861792389851089500547852915763547646899857970659940475514927",
            "8809619201418684241029036556295591884232522813567928806176674235810410775604",
            "12764568073160656986674789706181758338655490354081965460240045247683040081962",
            "21502007337926341717114099094861709208431032111194678565440998870068188932610",
            "6676554273606654034460232727824636863338632772826173222585689559169300842540",
            "3138170934188033588407671000185359515289243280807075679810358484377717004344",
            "5016504702993786669228778886709524960531243371932953717103586353783767283841",
            "3641096259839778412296729683448541948339993242606085025349116868466429331109",
            "17482178485290445442249591236781385361832252325559581596476967807317491695738",
            "17159462194092251514229072648808575169874022757757552441138883401008323177315",
            "7191903234268516892114204272287340227826681638192854529199275252092439950293",
            "5945747129617066655054359784112681539348647904456722905528854333831147439943",
            "11682653935985309726471808915274638394951372080323090060070436784000986335305",
            "2116213598349300952598605376561162484274388090426753376198347878848540790895",
            "5714326248919187415740532589098943107423637397599181819843406048950342329379",
            "13894119751705485508983929457149987156694369489992252755933362006151149676448",
            "10319593038266123453300247039462513707023223679302391278432798959473214716610",
            "1128983626080142661579089137513406106577305284945391649710553073832876332136",
            "4248221674033135716761210686080451495544280437155108649667019402496077376836",
            "838734091064411908005800793077104281843536168985419652740371543899822735427",
            "5199375564065532653333317325418032515582457298266061759973576494056772335768",
            "15300100374635143049391673582783434554769070281785839589894321842312801791719",
            "1529479817569769913729209110401024980435414116932327874985316118115320812957",
            "15270665240183241039904197262371028528545133272760122628694554835599635383702",
            "5641557314750776584122438294951634757985170942845644455628527989761038140088",
            "16326288709402544922431865006266288658569438060902755495235802091617779198057",
        ];

        let mut result = [[0u8; 32]; MIMC_ROUNDS];
        for i in 0..MIMC_ROUNDS {
            result[i] = decimal_to_be_bytes(constants_decimal[i]);
        }
        result
    }

    /// Convert decimal string to 32-byte big-endian representation
    /// Simple implementation for compile-time constant conversion
    fn decimal_to_be_bytes(s: &str) -> [u8; 32] {
        let mut result = [0u8; 32];
        let mut val = [0u64; 4]; // little-endian limbs
        for ch in s.bytes() {
            let digit = (ch - b'0') as u128;
            // Multiply val by 10 and add digit
            let mut carry = digit;
            for limb in val.iter_mut() {
                let v = (*limb as u128) * 10 + carry;
                *limb = v as u64;
                carry = v >> 64;
            }
        }
        let u = U256(val);
        u.to_be_bytes()
    }

    /// 29 level-specific IVs for the MiMC Merkle tree
    fn level_iv(level: usize) -> [u8; 32] {
        let ivs: [[u8; 32]; 29] = [
            decimal_to_be_bytes("149674538925118052205057075966660054952481571156186698930522557832224430770"),
            decimal_to_be_bytes("9670701465464311903249220692483401938888498641874948577387207195814981706974"),
            decimal_to_be_bytes("18318710344500308168304415114839554107298291987930233567781901093928276468271"),
            decimal_to_be_bytes("6597209388525824933845812104623007130464197923269180086306970975123437805179"),
            decimal_to_be_bytes("21720956803147356712695575768577036859892220417043839172295094119877855004262"),
            decimal_to_be_bytes("10330261616520855230513677034606076056972336573153777401182178891807369896722"),
            decimal_to_be_bytes("17466547730316258748333298168566143799241073466140136663575045164199607937939"),
            decimal_to_be_bytes("18881017304615283094648494495339883533502299318365959655029893746755475886610"),
            decimal_to_be_bytes("21580915712563378725413940003372103925756594604076607277692074507345076595494"),
            decimal_to_be_bytes("12316305934357579015754723412431647910012873427291630993042374701002287130550"),
            decimal_to_be_bytes("18905410889238873726515380969411495891004493295170115920825550288019118582494"),
            decimal_to_be_bytes("12819107342879320352602391015489840916114959026915005817918724958237245903353"),
            decimal_to_be_bytes("8245796392944118634696709403074300923517437202166861682117022548371601758802"),
            decimal_to_be_bytes("16953062784314687781686527153155644849196472783922227794465158787843281909585"),
            decimal_to_be_bytes("19346880451250915556764413197424554385509847473349107460608536657852472800734"),
            decimal_to_be_bytes("14486794857958402714787584825989957493343996287314210390323617462452254101347"),
            decimal_to_be_bytes("11127491343750635061768291849689189917973916562037173191089384809465548650641"),
            decimal_to_be_bytes("12217916643258751952878742936579902345100885664187835381214622522318889050675"),
            decimal_to_be_bytes("722025110834410790007814375535296040832778338853544117497481480537806506496"),
            decimal_to_be_bytes("15115624438829798766134408951193645901537753720219896384705782209102859383951"),
            decimal_to_be_bytes("11495230981884427516908372448237146604382590904456048258839160861769955046544"),
            decimal_to_be_bytes("16867999085723044773810250829569850875786210932876177117428755424200948460050"),
            decimal_to_be_bytes("1884116508014449609846749684134533293456072152192763829918284704109129550542"),
            decimal_to_be_bytes("14643335163846663204197941112945447472862168442334003800621296569318670799451"),
            decimal_to_be_bytes("1933387276732345916104540506251808516402995586485132246682941535467305930334"),
            decimal_to_be_bytes("7286414555941977227951257572976885370489143210539802284740420664558593616067"),
            decimal_to_be_bytes("16932161189449419608528042274282099409408565503929504242784173714823499212410"),
            decimal_to_be_bytes("16562533130736679030886586765487416082772837813468081467237161865787494093536"),
            decimal_to_be_bytes("6037428193077828806710267464232314380014232668931818917272972397574634037180"),
        ];
        ivs[level]
    }

    /// MiMC cipher: E_k(x) with x^7 exponent and 91 rounds
    fn mimc_cipher(in_x: &[u8; 32], in_k: &[u8; 32]) -> [u8; 32] {
        let p = U256(SCALAR_FIELD);
        let mut x = U256::from_be_bytes(in_x);
        let k = U256::from_be_bytes(in_k);
        let constants = mimc_round_constants();

        for i in 0..MIMC_ROUNDS {
            let c = U256::from_be_bytes(&constants[i]);
            // t = x + c + k
            let t = addmod(&addmod(&x, &c, &p), &k, &p);
            // t^7 = t * (t^2)^3
            let t2 = mulmod(&t, &t, &p);
            let t4 = mulmod(&t2, &t2, &p);
            let t6 = mulmod(&t4, &t2, &p);
            x = mulmod(&t6, &t, &p);
        }

        // Final key addition
        addmod(&x, &k, &p).to_be_bytes()
    }

    /// MiMC hash with Miyaguchi-Preneel compression and custom IV
    /// h = E_k(x) + x + k where k = running state
    fn mimc_hash_with_iv(data: &[[u8; 32]], iv: &[u8; 32]) -> [u8; 32] {
        let p = U256(SCALAR_FIELD);
        let mut r = U256::from_be_bytes(iv);

        for item in data {
            let x = U256::from_be_bytes(item);
            let r_bytes = r.to_be_bytes();
            let h = U256::from_be_bytes(&mimc_cipher(item, &r_bytes));
            // r = r + x + h
            r = addmod(&addmod(&r, &x, &p), &h, &p);
        }

        r.to_be_bytes()
    }

    /// MiMC hash with IV=0
    fn mimc_hash(data: &[[u8; 32]]) -> [u8; 32] {
        mimc_hash_with_iv(data, &[0u8; 32])
    }

    /// Hash two children at a given Merkle tree level
    fn merkle_hash(level: usize, left: &[u8; 32], right: &[u8; 32]) -> [u8; 32] {
        let iv = level_iv(level);
        mimc_hash_with_iv(&[*left, *right], &iv)
    }

    // =========================================================================
    //                        CONTRACT STORAGE
    // =========================================================================

    #[ink(storage)]
    pub struct MiximusPolkadot {
        denomination: Balance,
        asset_symbol: Vec<u8>,
        owner: AccountId,
        oracle: AccountId,
        next_leaf_index: u64,
        current_root: [u8; 32],
        nullifiers: Mapping<[u8; 32], bool>,
        roots: Mapping<[u8; 32], bool>,
        /// Full node storage: key = (level, index) encoded as u64, value = hash
        tree_nodes: Mapping<u64, [u8; 32]>,
        /// Zero hashes for each level (uninitialized nodes)
        zero_hashes: Mapping<u32, [u8; 32]>,
        /// Oracle attestations for proof verification
        attestations: Mapping<[u8; 32], bool>,
        vk_data: Vec<u8>,
    }

    #[ink(event)]
    pub struct Deposit {
        #[ink(topic)]
        leaf_hash: [u8; 32],
        #[ink(topic)]
        leaf_index: u64,
        timestamp: u64,
    }

    #[ink(event)]
    pub struct Withdrawal {
        #[ink(topic)]
        recipient: AccountId,
        nullifier: [u8; 32],
        timestamp: u64,
    }

    #[ink(event)]
    pub struct ProofAttested {
        #[ink(topic)]
        pub_hash: [u8; 32],
        oracle: AccountId,
    }

    #[derive(Debug, PartialEq, Eq)]
    #[ink::scale_derive(Encode, Decode, TypeInfo)]
    pub enum Error {
        WrongDenomination,
        TreeFull,
        DoubleSpend,
        UnknownRoot,
        InvalidProof,
        TransferFailed,
        FeeTooHigh,
        NotOracle,
        NotOwner,
        InvalidBatchSize,
    }

    pub type Result<T> = core::result::Result<T, Error>;

    impl MiximusPolkadot {
        #[ink(constructor)]
        pub fn new(
            denomination: Balance,
            asset_symbol: Vec<u8>,
            oracle: AccountId,
            vk_data: Vec<u8>,
        ) -> Self {
            let mut instance = Self {
                denomination,
                asset_symbol,
                owner: Self::env().caller(),
                oracle,
                next_leaf_index: 0,
                current_root: [0u8; 32],
                nullifiers: Mapping::default(),
                roots: Mapping::default(),
                tree_nodes: Mapping::default(),
                zero_hashes: Mapping::default(),
                attestations: Mapping::default(),
                vk_data,
            };
            instance.init_merkle_tree();
            instance
        }

        /// Deposit native currency into the mixer
        #[ink(message, payable)]
        pub fn deposit(&mut self, leaf_hash: [u8; 32]) -> Result<(u64, [u8; 32])> {
            let value = self.env().transferred_value();
            if value != self.denomination {
                return Err(Error::WrongDenomination);
            }
            if self.next_leaf_index >= MAX_LEAVES {
                return Err(Error::TreeFull);
            }

            let leaf_index = self.next_leaf_index;
            self.next_leaf_index += 1;

            let new_root = self.insert_leaf(leaf_hash, leaf_index as usize);
            self.current_root = new_root;
            self.roots.insert(new_root, &true);

            self.env().emit_event(Deposit {
                leaf_hash,
                leaf_index,
                timestamp: self.env().block_timestamp(),
            });

            Ok((leaf_index, new_root))
        }

        /// Batch deposit — deposit N units in a single transaction
        #[ink(message, payable)]
        pub fn batch_deposit(&mut self, leaf_hashes: Vec<[u8; 32]>) -> Result<Vec<(u64, [u8; 32])>> {
            let count = leaf_hashes.len();
            if count == 0 || count > 20 {
                return Err(Error::InvalidBatchSize);
            }

            let total_value = self.denomination * count as u128;
            let value = self.env().transferred_value();
            if value != total_value {
                return Err(Error::WrongDenomination);
            }

            let mut results = Vec::new();
            for leaf_hash in leaf_hashes {
                if self.next_leaf_index >= MAX_LEAVES {
                    return Err(Error::TreeFull);
                }

                let leaf_index = self.next_leaf_index;
                self.next_leaf_index += 1;

                let new_root = self.insert_leaf(leaf_hash, leaf_index as usize);
                self.current_root = new_root;
                self.roots.insert(new_root, &true);

                self.env().emit_event(Deposit {
                    leaf_hash,
                    leaf_index,
                    timestamp: self.env().block_timestamp(),
                });

                results.push((leaf_index, new_root));
            }

            Ok(results)
        }

        /// Withdraw using zkSNARK proof (oracle-verified)
        #[ink(message)]
        pub fn withdraw(
            &mut self,
            root: [u8; 32],
            nullifier: [u8; 32],
            pub_hash: [u8; 32],
        ) -> Result<()> {
            let recipient = self.env().caller();

            if self.nullifiers.get(nullifier).unwrap_or(false) {
                return Err(Error::DoubleSpend);
            }
            if !self.roots.get(root).unwrap_or(false) {
                return Err(Error::UnknownRoot);
            }

            // Verify oracle has attested this proof
            if !self.attestations.get(pub_hash).unwrap_or(false) {
                return Err(Error::InvalidProof);
            }

            self.nullifiers.insert(nullifier, &true);
            // Remove used attestation
            self.attestations.insert(pub_hash, &false);

            self.env()
                .transfer(recipient, self.denomination)
                .map_err(|_| Error::TransferFailed)?;

            self.env().emit_event(Withdrawal {
                recipient,
                nullifier,
                timestamp: self.env().block_timestamp(),
            });

            Ok(())
        }

        /// Batch withdraw — process up to 5 withdrawals in a single transaction
        #[ink(message)]
        pub fn batch_withdraw(
            &mut self,
            roots: Vec<[u8; 32]>,
            nullifiers: Vec<[u8; 32]>,
            pub_hashes: Vec<[u8; 32]>,
        ) -> Result<()> {
            let count = roots.len();
            if count == 0 || count > 5 {
                return Err(Error::InvalidBatchSize);
            }
            if nullifiers.len() != count || pub_hashes.len() != count {
                return Err(Error::InvalidBatchSize);
            }

            let recipient = self.env().caller();

            for i in 0..count {
                if self.nullifiers.get(nullifiers[i]).unwrap_or(false) {
                    return Err(Error::DoubleSpend);
                }
                if !self.roots.get(roots[i]).unwrap_or(false) {
                    return Err(Error::UnknownRoot);
                }
                if !self.attestations.get(pub_hashes[i]).unwrap_or(false) {
                    return Err(Error::InvalidProof);
                }

                self.nullifiers.insert(nullifiers[i], &true);
                self.attestations.insert(pub_hashes[i], &false);

                self.env().emit_event(Withdrawal {
                    recipient,
                    nullifier: nullifiers[i],
                    timestamp: self.env().block_timestamp(),
                });
            }

            // Transfer total amount
            let total = self.denomination * count as u128;
            self.env()
                .transfer(recipient, total)
                .map_err(|_| Error::TransferFailed)?;

            Ok(())
        }

        /// Withdraw via relayer to a specified recipient
        #[ink(message)]
        pub fn withdraw_via_relayer(
            &mut self,
            root: [u8; 32],
            nullifier: [u8; 32],
            pub_hash: [u8; 32],
            recipient: AccountId,
            relayer_fee: Balance,
        ) -> Result<()> {
            if relayer_fee >= self.denomination {
                return Err(Error::FeeTooHigh);
            }
            if self.nullifiers.get(nullifier).unwrap_or(false) {
                return Err(Error::DoubleSpend);
            }
            if !self.roots.get(root).unwrap_or(false) {
                return Err(Error::UnknownRoot);
            }
            if !self.attestations.get(pub_hash).unwrap_or(false) {
                return Err(Error::InvalidProof);
            }

            self.nullifiers.insert(nullifier, &true);
            self.attestations.insert(pub_hash, &false);

            let relayer = self.env().caller();
            if relayer_fee > 0 {
                self.env()
                    .transfer(relayer, relayer_fee)
                    .map_err(|_| Error::TransferFailed)?;
            }
            self.env()
                .transfer(recipient, self.denomination - relayer_fee)
                .map_err(|_| Error::TransferFailed)?;

            self.env().emit_event(Withdrawal {
                recipient,
                nullifier,
                timestamp: self.env().block_timestamp(),
            });

            Ok(())
        }

        /// Submit a proof attestation (oracle only)
        /// The oracle verifies the Groth16 BN254 proof off-chain and
        /// submits the public input hash as attestation.
        #[ink(message)]
        pub fn submit_proof_attestation(&mut self, pub_hash: [u8; 32]) -> Result<()> {
            if self.env().caller() != self.oracle {
                return Err(Error::NotOracle);
            }
            self.attestations.insert(pub_hash, &true);
            self.env().emit_event(ProofAttested {
                pub_hash,
                oracle: self.env().caller(),
            });
            Ok(())
        }

        /// Update oracle address (owner only)
        #[ink(message)]
        pub fn set_oracle(&mut self, new_oracle: AccountId) -> Result<()> {
            if self.env().caller() != self.owner {
                return Err(Error::NotOwner);
            }
            self.oracle = new_oracle;
            Ok(())
        }

        // View methods
        #[ink(message)]
        pub fn get_root(&self) -> [u8; 32] {
            self.current_root
        }

        #[ink(message)]
        pub fn is_spent(&self, nullifier: [u8; 32]) -> bool {
            self.nullifiers.get(nullifier).unwrap_or(false)
        }

        #[ink(message)]
        pub fn get_denomination(&self) -> Balance {
            self.denomination
        }

        #[ink(message)]
        pub fn get_next_leaf_index(&self) -> u64 {
            self.next_leaf_index
        }

        #[ink(message)]
        pub fn get_oracle(&self) -> AccountId {
            self.oracle
        }

        // =====================================================================
        //                     INTERNAL: MERKLE TREE
        // =====================================================================

        /// Encode (level, index) into a single u64 key for storage
        fn tree_node_key(level: u32, index: u64) -> u64 {
            ((level as u64) << 40) | index
        }

        /// Get a node from the tree, returning the zero hash if uninitialized
        fn get_node(&self, level: u32, index: u64) -> [u8; 32] {
            let key = Self::tree_node_key(level, index);
            self.tree_nodes
                .get(key)
                .unwrap_or_else(|| self.zero_hashes.get(level).unwrap_or([0u8; 32]))
        }

        /// Initialize the Merkle tree with zero hashes computed via MiMC
        fn init_merkle_tree(&mut self) {
            let mut zero = [0u8; 32];
            for i in 0..TREE_DEPTH as u32 {
                self.zero_hashes.insert(i, &zero);
                zero = merkle_hash(i as usize, &zero, &zero);
            }
            self.current_root = zero;
            self.roots.insert(zero, &true);
        }

        /// Insert a leaf and recompute the path to the root using MiMC
        fn insert_leaf(&mut self, leaf: [u8; 32], index: usize) -> [u8; 32] {
            // Store leaf at level 0
            let key = Self::tree_node_key(0, index as u64);
            self.tree_nodes.insert(key, &leaf);

            let mut current = leaf;
            let mut idx = index;

            for level in 0..TREE_DEPTH as u32 {
                let parent_idx = idx / 2;
                let (left, right) = if idx % 2 == 0 {
                    (current, self.get_node(level, idx as u64 + 1))
                } else {
                    (self.get_node(level, idx as u64 - 1), current)
                };

                current = merkle_hash(level as usize, &left, &right);

                // Store the parent node
                let parent_key = Self::tree_node_key(level + 1, parent_idx as u64);
                self.tree_nodes.insert(parent_key, &current);

                idx = parent_idx;
            }

            current
        }
    }
}
