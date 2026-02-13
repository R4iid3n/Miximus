/**
 * Miximus EOS Smart Contract (EOSIO/Antelope)
 *
 * zkSNARK-based mixer for EOS.
 * Written in C++ for the EOSIO/Antelope WASM runtime.
 *
 * MiMC Implementation:
 *   - x^7 exponent, 91 rounds, Miyaguchi-Preneel compression
 *   - Round constants hardcoded (EOSIO lacks native keccak256)
 *   - All arithmetic in BN254 scalar field using __int128 for intermediate
 *   - 29 level-specific IVs for Merkle tree
 *   - Full node Merkle tree with multi_index tables
 *
 * Proof verification:
 *   - Oracle-based (EOSIO/Antelope lacks BN254 pairing precompiles)
 *   - Trusted oracle submits attestation via attestproof action
 *
 * Supported: EOS (native)
 *
 * Copyright 2024 Miximus Authors — GPL-3.0-or-later
 */

#include <eosio/eosio.hpp>
#include <eosio/asset.hpp>
#include <eosio/crypto.hpp>
#include <eosio/system.hpp>

using namespace eosio;

constexpr uint32_t TREE_DEPTH = 29;
constexpr uint64_t MAX_LEAVES = 1ULL << TREE_DEPTH;
constexpr uint32_t MIMC_ROUNDS = 91;

// =========================================================================
//                    256-BIT FIELD ARITHMETIC
// =========================================================================
// BN254 scalar field: p = 21888242871839275222246405745257275088548364400416034343698204186575808495617
// Stored as 4 x uint64_t limbs (little-endian)

struct uint256_t {
    uint64_t limbs[4];

    uint256_t() : limbs{0, 0, 0, 0} {}
    uint256_t(uint64_t l0, uint64_t l1, uint64_t l2, uint64_t l3)
        : limbs{l0, l1, l2, l3} {}

    bool operator>=(const uint256_t& other) const {
        for (int i = 3; i >= 0; i--) {
            if (limbs[i] > other.limbs[i]) return true;
            if (limbs[i] < other.limbs[i]) return false;
        }
        return true; // equal
    }

    bool operator==(const uint256_t& other) const {
        return limbs[0] == other.limbs[0] && limbs[1] == other.limbs[1] &&
               limbs[2] == other.limbs[2] && limbs[3] == other.limbs[3];
    }

    bool is_zero() const {
        return limbs[0] == 0 && limbs[1] == 0 && limbs[2] == 0 && limbs[3] == 0;
    }
};

static const uint256_t SCALAR_FIELD(
    0x43e1f593f0000001ULL,
    0x2833e84879b97091ULL,
    0xb85045b68181585dULL,
    0x30644e72e131a029ULL
);

// Add with carry, returns (result, carry)
uint256_t add256(const uint256_t& a, const uint256_t& b, bool& carry) {
    uint256_t result;
    unsigned __int128 c = 0;
    for (int i = 0; i < 4; i++) {
        c += (unsigned __int128)a.limbs[i] + b.limbs[i];
        result.limbs[i] = (uint64_t)c;
        c >>= 64;
    }
    carry = (c != 0);
    return result;
}

// Subtract with borrow
uint256_t sub256(const uint256_t& a, const uint256_t& b, bool& borrow) {
    uint256_t result;
    __int128 c = 0;
    for (int i = 0; i < 4; i++) {
        c += (__int128)a.limbs[i] - b.limbs[i];
        result.limbs[i] = (uint64_t)c;
        c >>= 64;
    }
    borrow = (c < 0);
    return result;
}

// Modular addition: (a + b) mod p
uint256_t addmod(const uint256_t& a, const uint256_t& b) {
    bool carry;
    uint256_t sum = add256(a, b, carry);
    if (carry || sum >= SCALAR_FIELD) {
        bool b2;
        sum = sub256(sum, SCALAR_FIELD, b2);
    }
    return sum;
}

// Modular multiplication using 512-bit intermediate and shift-reduce
uint256_t mulmod(const uint256_t& a, const uint256_t& b) {
    uint64_t prod[8] = {0};
    for (int i = 0; i < 4; i++) {
        unsigned __int128 carry = 0;
        for (int j = 0; j < 4; j++) {
            unsigned __int128 v = (unsigned __int128)prod[i+j]
                + (unsigned __int128)a.limbs[i] * b.limbs[j] + carry;
            prod[i+j] = (uint64_t)v;
            carry = v >> 64;
        }
        prod[i+4] = (uint64_t)carry;
    }

    // Reduce 512 bits mod p using bit-by-bit shift and subtract
    uint256_t r;
    for (int i = 7; i >= 0; i--) {
        for (int bit = 63; bit >= 0; bit--) {
            // Shift r left by 1
            uint64_t carry = 0;
            for (int j = 0; j < 4; j++) {
                uint64_t nc = r.limbs[j] >> 63;
                r.limbs[j] = (r.limbs[j] << 1) | carry;
                carry = nc;
            }
            // Add bit
            uint64_t cur_bit = (prod[i] >> bit) & 1;
            if (cur_bit) {
                bool c;
                uint256_t one(1, 0, 0, 0);
                r = add256(r, one, c);
                if (c || r >= SCALAR_FIELD) {
                    bool b2;
                    r = sub256(r, SCALAR_FIELD, b2);
                }
            } else if (r >= SCALAR_FIELD) {
                bool b2;
                r = sub256(r, SCALAR_FIELD, b2);
            }
        }
    }
    return r;
}

// =========================================================================
//                      MiMC ROUND CONSTANTS
// =========================================================================
// 91 constants from keccak256 hash chain with seed keccak256("mimc").
// Hardcoded because EOSIO has no native keccak256.
// Each stored as 4 x uint64_t limbs (little-endian).

static const uint256_t MIMC_CONSTANTS[91] = {
    // These would be the precomputed limb representations.
    // For readability, we use a function to convert decimal strings at init time.
    // In production, these would be compile-time constants.
};

// Decimal string round constants (converted at runtime for clarity)
static const char* MIMC_CONSTANT_STRS[91] = {
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
};

// Level IVs for Merkle tree
static const char* LEVEL_IV_STRS[29] = {
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
};

// Parse decimal string to uint256_t
uint256_t parse_decimal(const char* s) {
    uint256_t result;
    while (*s) {
        // result = result * 10 + digit
        unsigned __int128 carry = (*s - '0');
        for (int i = 0; i < 4; i++) {
            unsigned __int128 v = (unsigned __int128)result.limbs[i] * 10 + carry;
            result.limbs[i] = (uint64_t)v;
            carry = v >> 64;
        }
        s++;
    }
    return result;
}

// =========================================================================
//                         MiMC IMPLEMENTATION
// =========================================================================

// MiMC cipher: E_k(x) with x^7 exponent and 91 rounds
uint256_t mimc_cipher(const uint256_t& x, const uint256_t& k) {
    uint256_t state = x;
    for (uint32_t i = 0; i < MIMC_ROUNDS; i++) {
        uint256_t c = parse_decimal(MIMC_CONSTANT_STRS[i]);
        uint256_t t = addmod(addmod(state, c), k);
        // t^7 = t * (t^2)^3
        uint256_t t2 = mulmod(t, t);
        uint256_t t4 = mulmod(t2, t2);
        uint256_t t6 = mulmod(t4, t2);
        state = mulmod(t6, t);
    }
    return addmod(state, k);
}

// MiMC hash with Miyaguchi-Preneel compression and custom IV
uint256_t mimc_hash_iv(const uint256_t& val1, const uint256_t& val2, const uint256_t& iv) {
    // First element
    uint256_t h = mimc_cipher(val1, iv);
    uint256_t r = addmod(addmod(iv, val1), h);
    // Second element
    h = mimc_cipher(val2, r);
    r = addmod(addmod(r, val2), h);
    return r;
}

// MiMC hash with IV=0
uint256_t mimc_hash(const uint256_t& val1, const uint256_t& val2) {
    uint256_t zero;
    return mimc_hash_iv(val1, val2, zero);
}

// MiMC hash of three values (for public input hashing)
uint256_t mimc_hash3(const uint256_t& v1, const uint256_t& v2, const uint256_t& v3) {
    uint256_t zero;
    uint256_t h = mimc_cipher(v1, zero);
    uint256_t r = addmod(addmod(zero, v1), h);
    h = mimc_cipher(v2, r);
    r = addmod(addmod(r, v2), h);
    h = mimc_cipher(v3, r);
    r = addmod(addmod(r, v3), h);
    return r;
}

// Level IV for Merkle tree
uint256_t get_level_iv(uint32_t level) {
    check(level < 29, "Invalid Merkle tree level");
    return parse_decimal(LEVEL_IV_STRS[level]);
}

// Hash two children at a given Merkle tree level
uint256_t merkle_hash(uint32_t level, const uint256_t& left, const uint256_t& right) {
    uint256_t iv = get_level_iv(level);
    return mimc_hash_iv(left, right, iv);
}

// Convert uint256_t to checksum256 (32-byte big-endian)
checksum256 to_checksum(const uint256_t& v) {
    uint8_t bytes[32] = {0};
    for (int i = 0; i < 4; i++) {
        int off = 24 - i * 8;
        uint64_t val = v.limbs[i];
        for (int j = 0; j < 8; j++) {
            bytes[off + j] = (val >> (56 - j * 8)) & 0xff;
        }
    }
    return checksum256(bytes);
}

// Convert checksum256 to uint256_t
uint256_t from_checksum(const checksum256& cs) {
    auto arr = cs.extract_as_byte_array();
    uint256_t result;
    for (int i = 0; i < 4; i++) {
        int off = 24 - i * 8;
        uint64_t val = 0;
        for (int j = 0; j < 8; j++) {
            val = (val << 8) | arr[off + j];
        }
        result.limbs[i] = val;
    }
    return result;
}

// =========================================================================
//                        CONTRACT
// =========================================================================

class [[eosio::contract("miximus")]] miximus : public contract {
public:
    using contract::contract;

    // =====================================================================
    //                      TABLE DEFINITIONS
    // =====================================================================

    struct [[eosio::table]] config {
        uint64_t id = 0;
        asset denomination;
        std::string asset_symbol;
        name owner;
        name oracle;
        uint64_t next_leaf_index;
        checksum256 current_root;
        std::vector<uint8_t> vk_data;

        uint64_t primary_key() const { return id; }
    };

    struct [[eosio::table]] nullifier_entry {
        uint64_t id;
        checksum256 nullifier;
        bool spent;

        uint64_t primary_key() const { return id; }
        checksum256 by_nullifier() const { return nullifier; }
    };

    struct [[eosio::table]] root_entry {
        uint64_t id;
        checksum256 root;
        bool valid;

        uint64_t primary_key() const { return id; }
        checksum256 by_root() const { return root; }
    };

    // Full tree node storage: (level, index) -> hash
    struct [[eosio::table]] treenode_entry {
        uint64_t id;          // compound key: (level << 40) | index
        checksum256 hash_val;

        uint64_t primary_key() const { return id; }
    };

    // Oracle attestations for proof verification
    struct [[eosio::table]] attestation_entry {
        uint64_t id;
        checksum256 pub_hash;
        bool valid;

        uint64_t primary_key() const { return id; }
        checksum256 by_hash() const { return pub_hash; }
    };

    typedef multi_index<"config"_n, config> config_table;
    typedef multi_index<"nullifiers"_n, nullifier_entry,
        indexed_by<"bynullifier"_n, const_mem_fun<nullifier_entry, checksum256, &nullifier_entry::by_nullifier>>
    > nullifier_table;
    typedef multi_index<"roots"_n, root_entry,
        indexed_by<"byroot"_n, const_mem_fun<root_entry, checksum256, &root_entry::by_root>>
    > root_table;
    typedef multi_index<"treenodes"_n, treenode_entry> treenode_table;
    typedef multi_index<"attestatn"_n, attestation_entry,
        indexed_by<"byhash"_n, const_mem_fun<attestation_entry, checksum256, &attestation_entry::by_hash>>
    > attestation_table;

    // =====================================================================
    //                        ACTIONS
    // =====================================================================

    [[eosio::action]]
    void init(asset denomination, std::string asset_symbol,
              name oracle, std::vector<uint8_t> vk_data) {
        require_auth(get_self());

        config_table configs(get_self(), get_self().value);
        check(configs.find(0) == configs.end(), "Already initialized");

        // Compute initial root via MiMC zero hashes
        uint256_t zero;
        for (uint32_t i = 0; i < TREE_DEPTH; i++) {
            zero = merkle_hash(i, zero, zero);
        }

        configs.emplace(get_self(), [&](auto& c) {
            c.id = 0;
            c.denomination = denomination;
            c.asset_symbol = asset_symbol;
            c.owner = get_self();
            c.oracle = oracle;
            c.next_leaf_index = 0;
            c.current_root = to_checksum(zero);
            c.vk_data = vk_data;
        });

        // Mark initial root as valid
        root_table roots(get_self(), get_self().value);
        roots.emplace(get_self(), [&](auto& r) {
            r.id = 0;
            r.root = to_checksum(zero);
            r.valid = true;
        });
    }

    [[eosio::action]]
    void deposit(name depositor, checksum256 leaf_hash) {
        require_auth(depositor);

        config_table configs(get_self(), get_self().value);
        auto cfg = configs.get(0, "Not initialized");
        check(cfg.next_leaf_index < MAX_LEAVES, "Merkle tree full");

        // Transfer tokens
        action(
            permission_level{depositor, "active"_n},
            "eosio.token"_n, "transfer"_n,
            std::make_tuple(depositor, get_self(), cfg.denomination,
                           std::string("miximus deposit"))
        ).send();

        uint64_t leaf_index = cfg.next_leaf_index;
        uint256_t leaf = from_checksum(leaf_hash);

        // Insert leaf into full-node MiMC Merkle tree
        checksum256 new_root = insert_leaf(leaf, leaf_index);

        configs.modify(configs.find(0), get_self(), [&](auto& c) {
            c.next_leaf_index = leaf_index + 1;
            c.current_root = new_root;
        });

        root_table roots(get_self(), get_self().value);
        roots.emplace(get_self(), [&](auto& r) {
            r.id = roots.available_primary_key();
            r.root = new_root;
            r.valid = true;
        });
    }

    [[eosio::action]]
    void batchdeposit(name depositor, std::vector<checksum256> leaf_hashes) {
        require_auth(depositor);

        uint32_t count = leaf_hashes.size();
        check(count > 0 && count <= 20, "Batch size must be 1-20");

        config_table configs(get_self(), get_self().value);
        auto cfg = configs.get(0, "Not initialized");
        check(cfg.next_leaf_index + count <= MAX_LEAVES, "Merkle tree full");

        // Transfer total tokens
        asset total_amount = cfg.denomination * count;
        action(
            permission_level{depositor, "active"_n},
            "eosio.token"_n, "transfer"_n,
            std::make_tuple(depositor, get_self(), total_amount,
                           std::string("miximus batch deposit"))
        ).send();

        root_table roots(get_self(), get_self().value);

        for (uint32_t i = 0; i < count; i++) {
            uint64_t leaf_index = cfg.next_leaf_index + i;
            uint256_t leaf = from_checksum(leaf_hashes[i]);
            checksum256 new_root = insert_leaf(leaf, leaf_index);

            roots.emplace(get_self(), [&](auto& r) {
                r.id = roots.available_primary_key();
                r.root = new_root;
                r.valid = true;
            });

            if (i == count - 1) {
                configs.modify(configs.find(0), get_self(), [&](auto& c) {
                    c.next_leaf_index = leaf_index + 1;
                    c.current_root = new_root;
                });
            }
        }
    }

    [[eosio::action]]
    void attestproof(name oracle_account, checksum256 pub_hash) {
        require_auth(oracle_account);

        config_table configs(get_self(), get_self().value);
        auto cfg = configs.get(0, "Not initialized");
        check(oracle_account == cfg.oracle, "Not authorized oracle");

        attestation_table attestations(get_self(), get_self().value);
        attestations.emplace(get_self(), [&](auto& a) {
            a.id = attestations.available_primary_key();
            a.pub_hash = pub_hash;
            a.valid = true;
        });
    }

    [[eosio::action]]
    void withdraw(name recipient, checksum256 root, checksum256 nullifier,
                  checksum256 pub_hash) {
        require_auth(recipient);

        config_table configs(get_self(), get_self().value);
        auto cfg = configs.get(0, "Not initialized");

        // Check nullifier
        nullifier_table nullifiers(get_self(), get_self().value);
        auto null_idx = nullifiers.get_index<"bynullifier"_n>();
        check(null_idx.find(nullifier) == null_idx.end(), "Double-spend");

        // Check root
        root_table roots(get_self(), get_self().value);
        auto root_idx = roots.get_index<"byroot"_n>();
        check(root_idx.find(root) != root_idx.end(), "Unknown root");

        // Check oracle attestation
        attestation_table attestations(get_self(), get_self().value);
        auto att_idx = attestations.get_index<"byhash"_n>();
        auto att_itr = att_idx.find(pub_hash);
        check(att_itr != att_idx.end(), "No oracle attestation for this proof");

        // Mark nullifier
        nullifiers.emplace(get_self(), [&](auto& n) {
            n.id = nullifiers.available_primary_key();
            n.nullifier = nullifier;
            n.spent = true;
        });

        // Remove attestation
        att_idx.erase(att_itr);

        // Transfer to recipient
        action(
            permission_level{get_self(), "active"_n},
            "eosio.token"_n, "transfer"_n,
            std::make_tuple(get_self(), recipient, cfg.denomination,
                           std::string("miximus withdrawal"))
        ).send();
    }

    [[eosio::action]]
    void batchwithdraw(name recipient, std::vector<checksum256> roots,
                       std::vector<checksum256> nullifier_list,
                       std::vector<checksum256> pub_hashes) {
        require_auth(recipient);

        uint32_t count = roots.size();
        check(count > 0 && count <= 5, "Batch size must be 1-5");
        check(nullifier_list.size() == count, "Nullifiers length mismatch");
        check(pub_hashes.size() == count, "PubHashes length mismatch");

        config_table configs(get_self(), get_self().value);
        auto cfg = configs.get(0, "Not initialized");

        nullifier_table nullifiers(get_self(), get_self().value);
        auto null_idx = nullifiers.get_index<"bynullifier"_n>();

        root_table roots_tbl(get_self(), get_self().value);
        auto root_idx = roots_tbl.get_index<"byroot"_n>();

        attestation_table attestations(get_self(), get_self().value);
        auto att_idx = attestations.get_index<"byhash"_n>();

        for (uint32_t i = 0; i < count; i++) {
            // Check nullifier
            check(null_idx.find(nullifier_list[i]) == null_idx.end(), "Double-spend");

            // Check root
            check(root_idx.find(roots[i]) != root_idx.end(), "Unknown root");

            // Check oracle attestation
            auto att_itr = att_idx.find(pub_hashes[i]);
            check(att_itr != att_idx.end(), "No oracle attestation for this proof");

            // Mark nullifier
            nullifiers.emplace(get_self(), [&](auto& n) {
                n.id = nullifiers.available_primary_key();
                n.nullifier = nullifier_list[i];
                n.spent = true;
            });

            // Remove attestation
            att_idx.erase(att_itr);
        }

        // Transfer total to recipient
        asset total_amount = cfg.denomination * count;
        action(
            permission_level{get_self(), "active"_n},
            "eosio.token"_n, "transfer"_n,
            std::make_tuple(get_self(), recipient, total_amount,
                           std::string("miximus batch withdrawal"))
        ).send();
    }

    [[eosio::action]]
    void setoracle(name new_oracle) {
        config_table configs(get_self(), get_self().value);
        auto cfg = configs.get(0, "Not initialized");
        require_auth(cfg.owner);

        configs.modify(configs.find(0), get_self(), [&](auto& c) {
            c.oracle = new_oracle;
        });
    }

private:
    static uint64_t tree_node_key(uint32_t level, uint64_t index) {
        return ((uint64_t)level << 40) | index;
    }

    uint256_t get_tree_node(uint32_t level, uint64_t index) {
        treenode_table nodes(get_self(), get_self().value);
        uint64_t key = tree_node_key(level, index);
        auto itr = nodes.find(key);
        if (itr != nodes.end()) {
            return from_checksum(itr->hash_val);
        }
        return uint256_t(); // zero
    }

    void set_tree_node(uint32_t level, uint64_t index, const uint256_t& value) {
        treenode_table nodes(get_self(), get_self().value);
        uint64_t key = tree_node_key(level, index);
        auto itr = nodes.find(key);
        if (itr != nodes.end()) {
            nodes.modify(itr, get_self(), [&](auto& n) {
                n.hash_val = to_checksum(value);
            });
        } else {
            nodes.emplace(get_self(), [&](auto& n) {
                n.id = key;
                n.hash_val = to_checksum(value);
            });
        }
    }

    checksum256 insert_leaf(const uint256_t& leaf, uint64_t leaf_index) {
        // Store leaf at level 0
        set_tree_node(0, leaf_index, leaf);
        uint256_t current = leaf;
        uint64_t idx = leaf_index;

        for (uint32_t level = 0; level < TREE_DEPTH; level++) {
            uint64_t parent_idx = idx / 2;
            uint256_t left, right;

            if (idx % 2 == 0) {
                left = current;
                right = get_tree_node(level, idx + 1);
            } else {
                left = get_tree_node(level, idx - 1);
                right = current;
            }

            current = merkle_hash(level, left, right);
            set_tree_node(level + 1, parent_idx, current);
            idx = parent_idx;
        }

        return to_checksum(current);
    }
};
