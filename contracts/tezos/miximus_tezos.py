"""
Miximus Tezos Smart Contract (SmartPy)

zkSNARK-based mixer for Tezos (XTZ).
Written in SmartPy, compiled to Michelson for the Tezos VM.

MiMC Implementation:
  - x^7 exponent, 91 rounds, Miyaguchi-Preneel compression
  - keccak256 hash chain from seed "mimc" for round constants
  - 29 level-specific IVs for Merkle tree
  - SmartPy's sp.int handles arbitrary-precision integers natively

Proof verification:
  - Tezos has BLS12-381 natively but NOT BN254
  - The original Miximus circuit uses BN254 (alt_bn128)
  - For native verification, the circuit would need BLS12-381 regeneration
  - Current approach: oracle-based verification (or note BLS12-381 circuit needed)

Supported: XTZ (native)

Copyright 2024 Miximus Authors — GPL-3.0-or-later
"""

import smartpy as sp


@sp.module
def main():
    TREE_DEPTH: int = 29
    MAX_LEAVES: int = 2**29
    SCALAR_FIELD: int = 21888242871839275222246405745257275088548364400416034343698204186575808495617
    MIMC_ROUNDS: int = 91

    # =========================================================================
    #                      MiMC ROUND CONSTANTS
    # =========================================================================
    # 91 constants from keccak256 hash chain with seed keccak256("mimc"),
    # reduced mod SCALAR_FIELD.

    MIMC_CONSTANTS: list = [
        9699427722198585233576395554477836603696224056248062887534150762780491344964,
        11703485025028567684989973226085996971982211366514589794869047827993715158284,
        16047385151842759715883983147732529094829228988006114315106338214348641493684,
        13171044560831470721204611089017807586748478995617618605757094330776784097979,
        463481810611863887895788181329300079259271913906328008157226405515633707060,
        14172737021216375674608750505647811061638328766015439391923848653810108862588,
        6689253641270970867338559588710848917420486594299189953566661581223880803412,
        6206378175987060350257013170941207256607267189110167715983507598036299759965,
        1868042604362664669096366350611088510094968563432118553423582843551251304148,
        3800923262676983849094741417247145368534214456118022255739022670427323747241,
        21591653578493131795224521299603914344271257669274375926196191948855055965941,
        10138810537922542300776837825791273739833273537236869643130335662561281936350,
        21574990455760257279296102927467279097968749263922051042846339699523743272465,
        16413121409077715441301059134455418701149785095704101665410282589314114365979,
        18250165490760061617105180803396666700674782964557583105320693987373016905441,
        7502779237586675485986299191768705581745728775671111833683511364027159171547,
        1871191249878415346013267028522443901105779688422863746611768655449989698507,
        14227980513379364932114804248442005973014852536227916890481139769683689826355,
        3626911537588022011409641665074817121756047123479165039814180423250987306580,
        19236074515568966224364617729593024174260343399978065715191519989928891482976,
        18303998739805578246875337832148027492674021151790013986107100904482029912855,
        15029356798333672110948390526097772289805005615627335370974040111484189851218,
        14009969076553308167172322716790329101547548435494434267861550029341368702955,
        2474399186054189702290953445489943933900186003481592958790627091252800758972,
        8499363137467817080120995943388159435886438129064869562976936976416160626765,
        3721538106651623159107419551085379332003626724680311764467196000779836528731,
        21513636789136435447726989659244632115907105013743178557543258425580816693013,
        6413499256104003900741626312911121949489199328341000172772535477686526161933,
        2423296695146958228105381999662588996417033334902395826324000730015059834867,
        14226452914890638176054896095327353311080202567202372321925082986206459137544,
        6668382834823585601183694406027162564759781576975604144599361907050393232654,
        7684682799902615328244940431427150087576264917860561004999996369763189716339,
        1889098908550857440616721504788014180820394851645231730287772458817397711248,
        6790625100354137563247974716700975825598182679172705081021265590776550026003,
        14773642371467989182995352422864409987810184892360173574623635121892742318878,
        19281448673732014642910881470629992531175666415068593568792064770752528727527,
        14533954802572082864290492673227299700287092879109981683817768414021039892181,
        7201323559292680367910220192893057999794593519219870913061645135556363761573,
        6732093332172534276604522937404905062834700997517855193752580368599291894652,
        17933540691001452559591212829318968824204486615606043739961347674721175964688,
        15961428780882917777414183392499617830167889800177298058040067254864354220287,
        12736214278132568876546350800822513740641931888358727849082359697485160982736,
        3439545814879193145334860319308882824567292287099085145529516573177554898,
        4304870388935813588332366794108449982123835995998847432495865244184755242106,
        18271176884720092981015377059918454136111894884653348162306733411625184098874,
        251341252390741357756739920423555089029964239608738693075482944570024594299,
        7061267873969201870294342652138581026512927130814986082309102676881598814324,
        15210185781629509117331823557188083554772921877973145839484228940930659831750,
        9933623231487467132083483273870403237369290780152447195366060181388225747404,
        8860207495959673050021299042484291804204364210189770038730065043316249584034,
        8225607920290235351257457224426373001131595237198233026729554520653645104823,
        2101754597405698707301278803774189375304825984406927629163551182822992849211,
        4650809359262437639973871683963300301321123052952582481016111916526927963510,
        2819469806498716032331303763953858334192180747654195125067222852780007249613,
        10026181953811808826365146991560498259739127762700251538466935087699710718980,
        4068800227252222261356221780345265002310350839287995811420025897830262605550,
        7706556989153408298246769455370263501638954772224719089725449880345119864895,
        708143970965367424687385234288223247694427964053921277910837987862864278471,
        14675466731217481032178475947165924106635215526640697173147623987334826158887,
        2891548451588016327005422884294243001284598433952314748085541373140885524236,
        3248061135531730385352170229977825871322045066439582053613486809232947427425,
        20009604326387202734077903479052788729780477058651868498203471330807320243485,
        16777657208000185795670509937485592891624105910517450105614416248715035393568,
        4651836398927038829184868494635901984396480816917764202384582304105185756554,
        18751163994760169650397520229993366266478887832036941208422557666277977396759,
        12897721113527742861792389851089500547852915763547646899857970659940475514927,
        8809619201418684241029036556295591884232522813567928806176674235810410775604,
        12764568073160656986674789706181758338655490354081965460240045247683040081962,
        21502007337926341717114099094861709208431032111194678565440998870068188932610,
        6676554273606654034460232727824636863338632772826173222585689559169300842540,
        3138170934188033588407671000185359515289243280807075679810358484377717004344,
        5016504702993786669228778886709524960531243371932953717103586353783767283841,
        3641096259839778412296729683448541948339993242606085025349116868466429331109,
        17482178485290445442249591236781385361832252325559581596476967807317491695738,
        17159462194092251514229072648808575169874022757757552441138883401008323177315,
        7191903234268516892114204272287340227826681638192854529199275252092439950293,
        5945747129617066655054359784112681539348647904456722905528854333831147439943,
        11682653935985309726471808915274638394951372080323090060070436784000986335305,
        2116213598349300952598605376561162484274388090426753376198347878848540790895,
        5714326248919187415740532589098943107423637397599181819843406048950342329379,
        13894119751705485508983929457149987156694369489992252755933362006151149676448,
        10319593038266123453300247039462513707023223679302391278432798959473214716610,
        1128983626080142661579089137513406106577305284945391649710553073832876332136,
        4248221674033135716761210686080451495544280437155108649667019402496077376836,
        838734091064411908005800793077104281843536168985419652740371543899822735427,
        5199375564065532653333317325418032515582457298266061759973576494056772335768,
        15300100374635143049391673582783434554769070281785839589894321842312801791719,
        1529479817569769913729209110401024980435414116932327874985316118115320812957,
        15270665240183241039904197262371028528545133272760122628694554835599635383702,
        5641557314750776584122438294951634757985170942845644455628527989761038140088,
        16326288709402544922431865006266288658569438060902755495235802091617779198057,
    ]

    # =========================================================================
    #                      LEVEL IVs FOR MERKLE TREE
    # =========================================================================
    # 29 level-specific IVs matching the ethsnarks circuit.

    LEVEL_IVS: list = [
        149674538925118052205057075966660054952481571156186698930522557832224430770,
        9670701465464311903249220692483401938888498641874948577387207195814981706974,
        18318710344500308168304415114839554107298291987930233567781901093928276468271,
        6597209388525824933845812104623007130464197923269180086306970975123437805179,
        21720956803147356712695575768577036859892220417043839172295094119877855004262,
        10330261616520855230513677034606076056972336573153777401182178891807369896722,
        17466547730316258748333298168566143799241073466140136663575045164199607937939,
        18881017304615283094648494495339883533502299318365959655029893746755475886610,
        21580915712563378725413940003372103925756594604076607277692074507345076595494,
        12316305934357579015754723412431647910012873427291630993042374701002287130550,
        18905410889238873726515380969411495891004493295170115920825550288019118582494,
        12819107342879320352602391015489840916114959026915005817918724958237245903353,
        8245796392944118634696709403074300923517437202166861682117022548371601758802,
        16953062784314687781686527153155644849196472783922227794465158787843281909585,
        19346880451250915556764413197424554385509847473349107460608536657852472800734,
        14486794857958402714787584825989957493343996287314210390323617462452254101347,
        11127491343750635061768291849689189917973916562037173191089384809465548650641,
        12217916643258751952878742936579902345100885664187835381214622522318889050675,
        722025110834410790007814375535296040832778338853544117497481480537806506496,
        15115624438829798766134408951193645901537753720219896384705782209102859383951,
        11495230981884427516908372448237146604382590904456048258839160861769955046544,
        16867999085723044773810250829569850875786210932876177117428755424200948460050,
        1884116508014449609846749684134533293456072152192763829918284704109129550542,
        14643335163846663204197941112945447472862168442334003800621296569318670799451,
        1933387276732345916104540506251808516402995586485132246682941535467305930334,
        7286414555941977227951257572976885370489143210539802284740420664558593616067,
        16932161189449419608528042274282099409408565503929504242784173714823499212410,
        16562533130736679030886586765487416082772837813468081467237161865787494093536,
        6037428193077828806710267464232314380014232668931818917272972397574634037180,
    ]

    # =========================================================================
    #                         MiMC IMPLEMENTATION
    # =========================================================================

    class MiximusTezos(sp.Contract):
        """
        Tezos mixer contract with proper MiMC hash implementation.

        Storage:
          - denomination: Fixed XTZ amount (in mutez)
          - next_leaf_index: Next Merkle tree position
          - current_root: Merkle tree root (integer field element)
          - nullifiers: big_map of spent nullifiers
          - roots: big_map of valid roots
          - tree_nodes: big_map of (level, index) -> hash for full node storage
          - zero_hashes: big_map of level -> zero hash
          - oracle: trusted oracle address for proof verification
          - vk_data: Verifying key bytes
          - attestations: big_map of pub_hash -> bool for oracle attestations
        """

        def __init__(self, denomination, oracle, vk_data):
            self.data.denomination = denomination
            self.data.next_leaf_index = 0
            self.data.current_root = sp.int(0)
            self.data.nullifiers = sp.big_map(tkey=sp.TInt, tvalue=sp.TBool)
            self.data.roots = sp.big_map(tkey=sp.TInt, tvalue=sp.TBool)
            self.data.tree_nodes = sp.big_map(
                tkey=sp.TPair(sp.TInt, sp.TInt),  # (level, index)
                tvalue=sp.TInt
            )
            self.data.zero_hashes = sp.big_map(tkey=sp.TInt, tvalue=sp.TInt)
            self.data.oracle = oracle
            self.data.vk_data = vk_data
            self.data.owner = sp.sender
            self.data.attestations = sp.big_map(tkey=sp.TInt, tvalue=sp.TBool)

            # Initialize zero hashes using MiMC
            zero = 0
            for level in range(TREE_DEPTH):
                self.data.zero_hashes[level] = zero
                zero = self._merkle_hash(level, zero, zero)
            self.data.current_root = zero
            self.data.roots[zero] = True

        # =====================================================================
        #                    MiMC CIPHER AND HASH
        # =====================================================================

        @sp.private_lambda(with_storage="read-only")
        def _mimc_cipher(self, x, k):
            """
            MiMC cipher: E_k(x) with x^7 exponent and 91 rounds.
            Round: t = x + c_i + k (mod p); x = t^7 (mod p)
            Final: return x + k (mod p)

            SmartPy handles arbitrary-precision integer arithmetic natively.
            """
            result = x
            for i in range(MIMC_ROUNDS):
                c = MIMC_CONSTANTS[i]
                t = (result + c + k) % SCALAR_FIELD
                # t^7 = t * (t^2)^3
                t2 = (t * t) % SCALAR_FIELD
                t4 = (t2 * t2) % SCALAR_FIELD
                t6 = (t4 * t2) % SCALAR_FIELD
                result = (t6 * t) % SCALAR_FIELD
            return (result + k) % SCALAR_FIELD

        @sp.private_lambda(with_storage="read-only")
        def _mimc_hash_with_iv(self, data_list, iv):
            """
            MiMC hash using Miyaguchi-Preneel compression.
            h = E_k(x) + x + k, where k = running state (starts at IV).
            """
            r = iv
            for x in data_list:
                h = self._mimc_cipher(x, r)
                r = (r + x + h) % SCALAR_FIELD
            return r

        @sp.private_lambda(with_storage="read-only")
        def _mimc_hash(self, data_list):
            """MiMC hash with IV=0"""
            return self._mimc_hash_with_iv(data_list, 0)

        @sp.private_lambda(with_storage="read-only")
        def _get_level_iv(self, level):
            """Get level-specific IV for Merkle tree hashing"""
            return LEVEL_IVS[level]

        @sp.private_lambda(with_storage="read-only")
        def _merkle_hash(self, level, left, right):
            """Hash two children at a given Merkle tree level"""
            iv = self._get_level_iv(level)
            return self._mimc_hash_with_iv([left, right], iv)

        # =====================================================================
        #                    MERKLE TREE (FULL NODE)
        # =====================================================================

        @sp.private_lambda(with_storage="read-write")
        def _get_node(self, level, index):
            """Get node from tree, return zero hash if uninitialized"""
            key = sp.pair(level, index)
            if self.data.tree_nodes.contains(key):
                return self.data.tree_nodes[key]
            return self.data.zero_hashes.get(level, default_value=0)

        @sp.private_lambda(with_storage="read-write")
        def _insert_leaf(self, leaf_hash, leaf_index):
            """Insert leaf and recompute path to root"""
            # Store leaf at level 0
            self.data.tree_nodes[sp.pair(0, leaf_index)] = leaf_hash
            current = leaf_hash
            idx = leaf_index

            for level in range(TREE_DEPTH):
                parent_idx = idx // 2
                if idx % 2 == 0:
                    left = current
                    right = self._get_node(level, idx + 1)
                else:
                    left = self._get_node(level, idx - 1)
                    right = current

                current = self._merkle_hash(level, left, right)
                self.data.tree_nodes[sp.pair(level + 1, parent_idx)] = current
                idx = parent_idx

            return current

        # =====================================================================
        #                        ENTRYPOINTS
        # =====================================================================

        @sp.entrypoint
        def deposit(self, leaf_hash):
            """Deposit XTZ into the mixer"""
            sp.cast(leaf_hash, sp.TInt)

            # Verify correct amount
            assert sp.amount == self.data.denomination, "Must deposit exact denomination"
            assert self.data.next_leaf_index < MAX_LEAVES, "Merkle tree full"

            leaf_index = self.data.next_leaf_index
            self.data.next_leaf_index += 1

            # Insert leaf and compute new root using MiMC Merkle tree
            new_root = self._insert_leaf(leaf_hash, leaf_index)
            self.data.current_root = new_root
            self.data.roots[new_root] = True

        @sp.entrypoint
        def batch_deposit(self, leaf_hashes):
            """Batch deposit XTZ — deposit N units in a single transaction"""
            sp.cast(leaf_hashes, sp.TList(sp.TInt))

            count = sp.len(leaf_hashes)
            assert count > 0, "Empty batch"
            assert count <= 20, "Batch too large"
            assert sp.amount == self.data.denomination * count, "Must deposit exact total denomination"

            with sp.for_("leaf_hash", leaf_hashes) as leaf_hash:
                assert self.data.next_leaf_index < MAX_LEAVES, "Merkle tree full"
                leaf_index = self.data.next_leaf_index
                self.data.next_leaf_index += 1
                new_root = self._insert_leaf(leaf_hash, leaf_index)
                self.data.current_root = new_root
                self.data.roots[new_root] = True

        @sp.entrypoint
        def submit_attestation(self, pub_hash):
            """Oracle submits proof attestation (oracle only)"""
            sp.cast(pub_hash, sp.TInt)
            assert sp.sender == self.data.oracle, "Only oracle can submit attestations"
            self.data.attestations[pub_hash] = True

        @sp.entrypoint
        def set_oracle(self, new_oracle):
            """Update oracle address (owner only)"""
            sp.cast(new_oracle, sp.TAddress)
            assert sp.sender == self.data.owner, "Only owner"
            self.data.oracle = new_oracle

        @sp.entrypoint
        def withdraw(self, params):
            """
            Withdraw XTZ using zkSNARK proof (oracle-verified).

            params: record(root: int, nullifier: int, pub_hash: int)

            The pub_hash = MiMC(root, nullifier, ext_hash) is computed off-chain.
            The oracle verifies the Groth16 proof off-chain and submits an attestation.

            Note on native verification:
              Tezos supports BLS12-381 natively via Michelson opcodes:
                PAIRING_CHECK for bilinear pairing verification
              However, the Miximus circuit uses BN254. To use native Tezos
              verification, the circuit would need to be regenerated for BLS12-381.
              For now, we use oracle-based verification.
            """
            sp.cast(params, sp.TRecord(
                root=sp.TInt,
                nullifier=sp.TInt,
                pub_hash=sp.TInt,
            ))

            # Check nullifier not spent
            assert not self.data.nullifiers.contains(params.nullifier), "Double-spend"

            # Check root is known
            assert self.data.roots.contains(params.root), "Unknown root"

            # Verify oracle attestation
            assert self.data.attestations.contains(params.pub_hash), "No oracle attestation"

            # Mark nullifier
            self.data.nullifiers[params.nullifier] = True

            # Remove used attestation
            del self.data.attestations[params.pub_hash]

            # Transfer XTZ to sender
            sp.send(sp.sender, self.data.denomination)

        @sp.entrypoint
        def batch_withdraw(self, params):
            """Batch withdraw XTZ — process up to 5 withdrawals in a single transaction (oracle-verified)"""
            sp.cast(params, sp.TRecord(
                roots=sp.TList(sp.TInt),
                nullifiers=sp.TList(sp.TInt),
                pub_hashes=sp.TList(sp.TInt),
            ))

            count = sp.len(params.roots)
            assert count > 0, "Empty batch"
            assert count <= 5, "Batch too large (max 5)"
            assert sp.len(params.nullifiers) == count, "Nullifiers length mismatch"
            assert sp.len(params.pub_hashes) == count, "Pub hashes length mismatch"

            i = sp.local("i", 0)
            with sp.while_(i.value < count):
                root = params.roots[i.value]
                nullifier = params.nullifiers[i.value]
                pub_hash = params.pub_hashes[i.value]

                assert not self.data.nullifiers.contains(nullifier), "Double-spend"
                assert self.data.roots.contains(root), "Unknown root"
                assert self.data.attestations.contains(pub_hash), "No oracle attestation"

                self.data.nullifiers[nullifier] = True
                del self.data.attestations[pub_hash]

                i.value += 1

            # Transfer total amount to sender
            sp.send(sp.sender, self.data.denomination * count)

        @sp.entrypoint
        def withdraw_via_relayer(self, params):
            """Withdraw to specified recipient with relayer fee (oracle-verified)"""
            sp.cast(params, sp.TRecord(
                root=sp.TInt,
                nullifier=sp.TInt,
                pub_hash=sp.TInt,
                recipient=sp.TAddress,
                relayer_fee=sp.TMutez,
            ))

            assert params.relayer_fee < self.data.denomination, "Fee too high"
            assert not self.data.nullifiers.contains(params.nullifier), "Double-spend"
            assert self.data.roots.contains(params.root), "Unknown root"
            assert self.data.attestations.contains(params.pub_hash), "No oracle attestation"

            self.data.nullifiers[params.nullifier] = True
            del self.data.attestations[params.pub_hash]

            # Pay relayer
            if params.relayer_fee > sp.mutez(0):
                sp.send(sp.sender, params.relayer_fee)

            # Pay recipient
            remaining = self.data.denomination - params.relayer_fee
            sp.send(params.recipient, remaining)

        # =====================================================================
        #                        VIEW METHODS
        # =====================================================================

        @sp.onchain_view
        def get_root(self):
            return self.data.current_root

        @sp.onchain_view
        def is_spent(self, nullifier):
            sp.cast(nullifier, sp.TInt)
            return self.data.nullifiers.contains(nullifier)

        @sp.onchain_view
        def get_denomination(self):
            return self.data.denomination

        @sp.onchain_view
        def get_oracle(self):
            return self.data.oracle

        @sp.onchain_view
        def compute_mimc_hash(self, params):
            """Compute MiMC hash of two values (for off-chain verification)"""
            sp.cast(params, sp.TRecord(a=sp.TInt, b=sp.TInt))
            return self._mimc_hash([params.a, params.b])

        @sp.onchain_view
        def compute_merkle_hash(self, params):
            """Compute Merkle hash at a given level"""
            sp.cast(params, sp.TRecord(level=sp.TInt, left=sp.TInt, right=sp.TInt))
            return self._merkle_hash(params.level, params.left, params.right)


# Test
@sp.add_test()
def test_miximus_tezos():
    scenario = sp.test_scenario("MiximusTezos", main)

    # Deploy
    mixer = main.MiximusTezos(
        denomination=sp.mutez(1_000_000),  # 1 XTZ
        oracle=sp.address("tz1oracle..."),
        vk_data=sp.bytes("0x00"),
    )
    scenario += mixer

    # Deposit (using a MiMC leaf hash)
    mixer.deposit(12345).run(
        sender=sp.address("tz1depositor..."),
        amount=sp.mutez(1_000_000),
    )
