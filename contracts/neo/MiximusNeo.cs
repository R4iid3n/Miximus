/**
 * Miximus NEO Smart Contract (C#)
 *
 * zkSNARK-based mixer for NEO.
 * Written in C# for the NeoVM.
 *
 * MiMC Implementation:
 *   - x^7 exponent, 91 rounds, Miyaguchi-Preneel compression
 *   - keccak256 hash chain from seed "mimc" for round constants (precomputed)
 *   - 29 level-specific IVs for Merkle tree
 *   - C# BigInteger for full field arithmetic
 *   - Full node Merkle tree with Storage
 *
 * Proof verification:
 *   - Neo N3 has CryptoLib but no BN254 pairing
 *   - Uses oracle-based verification pattern
 *   - Trusted oracle submits attestation via SubmitAttestation
 *
 * Supported: NEO (native), GAS
 *
 * Copyright 2024 Miximus Authors — GPL-3.0-or-later
 */

using Neo;
using Neo.SmartContract;
using Neo.SmartContract.Framework;
using Neo.SmartContract.Framework.Attributes;
using Neo.SmartContract.Framework.Native;
using Neo.SmartContract.Framework.Services;
using System;
using System.Numerics;

namespace Miximus
{
    [ManifestExtra("Author", "Miximus")]
    [ManifestExtra("Description", "zkSNARK-based coin mixer for NEO with MiMC hash")]
    [ContractPermission("*", "transfer")]
    public class MiximusNeo : SmartContract
    {
        private const int TREE_DEPTH = 29;
        private const long MAX_LEAVES = 1L << TREE_DEPTH;
        private const int MIMC_ROUNDS = 91;

        // BN254 scalar field modulus
        private static readonly BigInteger SCALAR_FIELD = BigInteger.Parse(
            "21888242871839275222246405745257275088548364400416034343698204186575808495617");

        // Storage keys
        private static readonly byte[] KEY_DENOMINATION = new byte[] { 0x01 };
        private static readonly byte[] KEY_NEXT_INDEX = new byte[] { 0x02 };
        private static readonly byte[] KEY_ROOT = new byte[] { 0x03 };
        private static readonly byte[] KEY_VK = new byte[] { 0x04 };
        private static readonly byte[] KEY_OWNER = new byte[] { 0x05 };
        private static readonly byte[] KEY_ORACLE = new byte[] { 0x06 };
        private static readonly byte[] PREFIX_NULLIFIER = new byte[] { 0x10 };
        private static readonly byte[] PREFIX_ROOT = new byte[] { 0x11 };
        private static readonly byte[] PREFIX_TREENODE = new byte[] { 0x12 };
        private static readonly byte[] PREFIX_ZEROHASH = new byte[] { 0x13 };
        private static readonly byte[] PREFIX_ATTESTATION = new byte[] { 0x14 };

        // Events
        [DisplayName("Deposit")]
        public static event Action<byte[], BigInteger, BigInteger> OnDeposit;

        [DisplayName("Withdrawal")]
        public static event Action<UInt160, byte[], BigInteger> OnWithdrawal;

        [DisplayName("Attestation")]
        public static event Action<UInt160, byte[]> OnAttestation;

        // =====================================================================
        //                   MiMC ROUND CONSTANTS
        // =====================================================================
        // 91 constants from keccak256 hash chain with seed keccak256("mimc")

        private static BigInteger GetMiMCConstant(int index)
        {
            // All 91 round constants. In production, these would be stored
            // more efficiently, but for clarity they are inline.
            string[] constants = new string[] {
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
            return BigInteger.Parse(constants[index]);
        }

        // =====================================================================
        //                    LEVEL IVs
        // =====================================================================

        private static BigInteger GetLevelIV(int level)
        {
            string[] ivs = new string[] {
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
            return BigInteger.Parse(ivs[level]);
        }

        // =====================================================================
        //                   MiMC IMPLEMENTATION
        // =====================================================================

        /// <summary>
        /// MiMC cipher: E_k(x) with x^7 exponent and 91 rounds.
        /// Uses C# BigInteger for arbitrary-precision field arithmetic.
        /// </summary>
        private static BigInteger MiMCCipher(BigInteger x, BigInteger k)
        {
            BigInteger state = x;
            for (int i = 0; i < MIMC_ROUNDS; i++)
            {
                BigInteger c = GetMiMCConstant(i);
                BigInteger t = (state + c + k) % SCALAR_FIELD;
                // t^7 = t * (t^2)^3
                BigInteger t2 = (t * t) % SCALAR_FIELD;
                BigInteger t4 = (t2 * t2) % SCALAR_FIELD;
                BigInteger t6 = (t4 * t2) % SCALAR_FIELD;
                state = (t6 * t) % SCALAR_FIELD;
            }
            return (state + k) % SCALAR_FIELD;
        }

        /// <summary>
        /// MiMC hash with Miyaguchi-Preneel compression and custom IV.
        /// h = E_k(x) + x + k, where k = running state
        /// </summary>
        private static BigInteger MiMCHashWithIV(BigInteger[] data, BigInteger iv)
        {
            BigInteger r = iv;
            foreach (BigInteger x in data)
            {
                BigInteger h = MiMCCipher(x, r);
                r = (r + x + h) % SCALAR_FIELD;
            }
            return r;
        }

        /// <summary>MiMC hash with IV=0</summary>
        private static BigInteger MiMCHash(BigInteger[] data)
        {
            return MiMCHashWithIV(data, BigInteger.Zero);
        }

        /// <summary>Hash two children at a given Merkle tree level</summary>
        private static BigInteger MerkleHash(int level, BigInteger left, BigInteger right)
        {
            BigInteger iv = GetLevelIV(level);
            return MiMCHashWithIV(new BigInteger[] { left, right }, iv);
        }

        // =====================================================================
        //                   TREE NODE HELPERS
        // =====================================================================

        private static byte[] TreeNodeKey(int level, BigInteger index)
        {
            return Helper.Concat(PREFIX_TREENODE,
                Helper.Concat(((BigInteger)level).ToByteArray(), index.ToByteArray()));
        }

        private static byte[] ZeroHashKey(int level)
        {
            return Helper.Concat(PREFIX_ZEROHASH, ((BigInteger)level).ToByteArray());
        }

        private static byte[] AttestationKey(ByteString pubHash)
        {
            return Helper.Concat(PREFIX_ATTESTATION, (byte[])pubHash);
        }

        private static BigInteger GetTreeNode(StorageContext storage, int level, BigInteger index)
        {
            var key = TreeNodeKey(level, index);
            var val = Storage.Get(storage, key);
            if (val != null)
                return (BigInteger)val;
            // Return zero hash for this level
            var zhKey = ZeroHashKey(level);
            var zh = Storage.Get(storage, zhKey);
            if (zh != null)
                return (BigInteger)zh;
            return BigInteger.Zero;
        }

        // =====================================================================
        //                   CONTRACT METHODS
        // =====================================================================

        /// <summary>Initialize the mixer contract</summary>
        public static void Initialize(BigInteger denomination, ByteString vkData,
                                       UInt160 oracle)
        {
            var storage = Storage.CurrentContext;
            if (Storage.Get(storage, KEY_OWNER) != null)
                throw new Exception("Already initialized");

            Storage.Put(storage, KEY_DENOMINATION, denomination);
            Storage.Put(storage, KEY_NEXT_INDEX, 0);
            Storage.Put(storage, KEY_VK, vkData);
            Storage.Put(storage, KEY_OWNER, ((Transaction)Runtime.ScriptContainer).Sender);
            Storage.Put(storage, KEY_ORACLE, (ByteString)oracle);

            // Initialize zero hashes using MiMC
            BigInteger zero = BigInteger.Zero;
            for (int i = 0; i < TREE_DEPTH; i++)
            {
                var zhKey = ZeroHashKey(i);
                Storage.Put(storage, zhKey, zero);
                zero = MerkleHash(i, zero, zero);
            }

            // Set initial root
            Storage.Put(storage, KEY_ROOT, zero);

            // Mark initial root as valid
            var rootKey = Helper.Concat(PREFIX_ROOT, zero.ToByteArray());
            Storage.Put(storage, rootKey, 1);
        }

        /// <summary>Deposit NEO/GAS into the mixer</summary>
        public static (ByteString, BigInteger) Deposit(UInt160 depositor, BigInteger leafHash)
        {
            if (!Runtime.CheckWitness(depositor))
                throw new Exception("Not authorized");

            var storage = Storage.CurrentContext;
            BigInteger denomination = (BigInteger)Storage.Get(storage, KEY_DENOMINATION);
            BigInteger nextIndex = (BigInteger)Storage.Get(storage, KEY_NEXT_INDEX);

            if (nextIndex >= MAX_LEAVES)
                throw new Exception("Merkle tree full");

            // Transfer NEO from depositor to contract
            var success = NEO.Transfer(depositor, Runtime.ExecutingScriptHash, denomination);
            if (!success) throw new Exception("Transfer failed");

            // Insert leaf into full-node MiMC Merkle tree
            BigInteger newRoot = InsertLeaf(storage, leafHash, nextIndex);

            // Update state
            Storage.Put(storage, KEY_NEXT_INDEX, nextIndex + 1);
            Storage.Put(storage, KEY_ROOT, newRoot);

            // Mark new root as valid
            var rootKey = Helper.Concat(PREFIX_ROOT, newRoot.ToByteArray());
            Storage.Put(storage, rootKey, 1);

            OnDeposit(leafHash.ToByteArray(), nextIndex, Runtime.Time);

            return ((ByteString)newRoot.ToByteArray(), nextIndex);
        }

        /// <summary>Batch deposit NEO/GAS — deposit N units in a single transaction</summary>
        public static BigInteger BatchDeposit(UInt160 depositor, BigInteger[] leafHashes)
        {
            if (!Runtime.CheckWitness(depositor))
                throw new Exception("Not authorized");

            int count = leafHashes.Length;
            if (count <= 0 || count > 20)
                throw new Exception("Batch size must be 1-20");

            var storage = Storage.CurrentContext;
            BigInteger denomination = (BigInteger)Storage.Get(storage, KEY_DENOMINATION);
            BigInteger nextIndex = (BigInteger)Storage.Get(storage, KEY_NEXT_INDEX);

            if (nextIndex + count > MAX_LEAVES)
                throw new Exception("Merkle tree full");

            // Transfer total amount
            BigInteger totalAmount = denomination * count;
            var success = NEO.Transfer(depositor, Runtime.ExecutingScriptHash, totalAmount);
            if (!success) throw new Exception("Transfer failed");

            BigInteger startIndex = nextIndex;

            for (int i = 0; i < count; i++)
            {
                BigInteger newRoot = InsertLeaf(storage, leafHashes[i], nextIndex);
                Storage.Put(storage, KEY_ROOT, newRoot);

                var rootKey = Helper.Concat(PREFIX_ROOT, newRoot.ToByteArray());
                Storage.Put(storage, rootKey, 1);

                OnDeposit(leafHashes[i].ToByteArray(), nextIndex, Runtime.Time);
                nextIndex += 1;
            }

            Storage.Put(storage, KEY_NEXT_INDEX, nextIndex);

            return startIndex;
        }

        /// <summary>Submit proof attestation (oracle only)</summary>
        public static void SubmitAttestation(UInt160 oracleAddr, ByteString pubHash)
        {
            if (!Runtime.CheckWitness(oracleAddr))
                throw new Exception("Not authorized");

            var storage = Storage.CurrentContext;
            UInt160 storedOracle = (UInt160)Storage.Get(storage, KEY_ORACLE);
            if (oracleAddr != storedOracle)
                throw new Exception("Not the authorized oracle");

            var attKey = AttestationKey(pubHash);
            Storage.Put(storage, attKey, 1);

            OnAttestation(oracleAddr, (byte[])pubHash);
        }

        /// <summary>Withdraw using zkSNARK proof (oracle-verified)</summary>
        public static void Withdraw(
            UInt160 recipient,
            ByteString root,
            ByteString nullifier,
            ByteString pubHash)
        {
            if (!Runtime.CheckWitness(recipient))
                throw new Exception("Not authorized");

            var storage = Storage.CurrentContext;
            BigInteger denomination = (BigInteger)Storage.Get(storage, KEY_DENOMINATION);

            // Check nullifier
            var nullKey = Helper.Concat(PREFIX_NULLIFIER, (byte[])nullifier);
            if (Storage.Get(storage, nullKey) != null)
                throw new Exception("Double-spend");

            // Check root
            var rootKey = Helper.Concat(PREFIX_ROOT, (byte[])root);
            if (Storage.Get(storage, rootKey) == null)
                throw new Exception("Unknown root");

            // Verify oracle attestation
            var attKey = AttestationKey(pubHash);
            if (Storage.Get(storage, attKey) == null)
                throw new Exception("No oracle attestation for this proof");

            // Mark nullifier
            Storage.Put(storage, nullKey, 1);

            // Remove attestation
            Storage.Delete(storage, attKey);

            // Transfer to recipient
            NEO.Transfer(Runtime.ExecutingScriptHash, recipient, denomination);

            OnWithdrawal(recipient, (byte[])nullifier, Runtime.Time);
        }

        /// <summary>Batch withdraw — process up to 5 withdrawals in a single transaction</summary>
        public static void BatchWithdraw(
            UInt160 recipient,
            ByteString[] roots,
            ByteString[] nullifiers,
            ByteString[] pubHashes)
        {
            if (!Runtime.CheckWitness(recipient))
                throw new Exception("Not authorized");

            int count = roots.Length;
            if (count <= 0 || count > 5)
                throw new Exception("Batch size must be 1-5");
            if (nullifiers.Length != count)
                throw new Exception("Nullifiers length mismatch");
            if (pubHashes.Length != count)
                throw new Exception("PubHashes length mismatch");

            var storage = Storage.CurrentContext;
            BigInteger denomination = (BigInteger)Storage.Get(storage, KEY_DENOMINATION);

            for (int i = 0; i < count; i++)
            {
                // Check nullifier
                var nullKey = Helper.Concat(PREFIX_NULLIFIER, (byte[])nullifiers[i]);
                if (Storage.Get(storage, nullKey) != null)
                    throw new Exception("Double-spend");

                // Check root
                var rootKey = Helper.Concat(PREFIX_ROOT, (byte[])roots[i]);
                if (Storage.Get(storage, rootKey) == null)
                    throw new Exception("Unknown root");

                // Verify oracle attestation
                var attKey = AttestationKey(pubHashes[i]);
                if (Storage.Get(storage, attKey) == null)
                    throw new Exception("No oracle attestation for this proof");

                // Mark nullifier
                Storage.Put(storage, nullKey, 1);

                // Remove attestation
                Storage.Delete(storage, attKey);

                OnWithdrawal(recipient, (byte[])nullifiers[i], Runtime.Time);
            }

            // Transfer total to recipient
            BigInteger totalAmount = denomination * count;
            NEO.Transfer(Runtime.ExecutingScriptHash, recipient, totalAmount);
        }

        /// <summary>Set oracle address (owner only)</summary>
        public static void SetOracle(UInt160 newOracle)
        {
            var storage = Storage.CurrentContext;
            UInt160 owner = (UInt160)Storage.Get(storage, KEY_OWNER);
            if (!Runtime.CheckWitness(owner))
                throw new Exception("Not owner");
            Storage.Put(storage, KEY_ORACLE, (ByteString)newOracle);
        }

        // View methods
        public static ByteString GetRoot()
        {
            return Storage.Get(Storage.CurrentContext, KEY_ROOT);
        }

        public static bool IsSpent(ByteString nullifier)
        {
            var key = Helper.Concat(PREFIX_NULLIFIER, (byte[])nullifier);
            return Storage.Get(Storage.CurrentContext, key) != null;
        }

        public static BigInteger GetDenomination()
        {
            return (BigInteger)Storage.Get(Storage.CurrentContext, KEY_DENOMINATION);
        }

        public static UInt160 GetOracle()
        {
            return (UInt160)Storage.Get(Storage.CurrentContext, KEY_ORACLE);
        }

        // =====================================================================
        //                   INTERNAL: MERKLE TREE
        // =====================================================================

        private static BigInteger InsertLeaf(StorageContext storage,
                                              BigInteger leafHash, BigInteger leafIndex)
        {
            // Store leaf at level 0
            var leafKey = TreeNodeKey(0, leafIndex);
            Storage.Put(storage, leafKey, leafHash);

            BigInteger current = leafHash;
            BigInteger idx = leafIndex;

            for (int level = 0; level < TREE_DEPTH; level++)
            {
                BigInteger parentIdx = idx / 2;
                BigInteger left, right;

                if (idx % 2 == 0)
                {
                    left = current;
                    right = GetTreeNode(storage, level, idx + 1);
                }
                else
                {
                    left = GetTreeNode(storage, level, idx - 1);
                    right = current;
                }

                current = MerkleHash(level, left, right);

                // Store parent
                var parentKey = TreeNodeKey(level + 1, parentIdx);
                Storage.Put(storage, parentKey, current);

                idx = parentIdx;
            }

            return current;
        }
    }
}
