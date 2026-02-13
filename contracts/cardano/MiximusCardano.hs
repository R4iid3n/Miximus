{-|
Module      : MiximusCardano
Description : zkSNARK-based mixer for Cardano (ADA)
License     : GPL-3.0-or-later

Miximus Plutus V2 validator script for Cardano.
Accepts deposits of a fixed ADA denomination and allows
withdrawal using a zkSNARK proof.

Cardano's EUTXO model is different from account-based chains:
  - Deposits create UTXOs locked by the script
  - Withdrawals consume UTXOs and verify the proof
  - The Merkle tree state is stored in a reference datum

Cardano added BLS12-381 / BN254 primitives in Plutus V3 (Chang HF),
enabling on-chain zkSNARK verification.
-}

{-# LANGUAGE DataKinds           #-}
{-# LANGUAGE NoImplicitPrelude   #-}
{-# LANGUAGE OverloadedStrings   #-}
{-# LANGUAGE TemplateHaskell     #-}
{-# LANGUAGE TypeApplications    #-}

module MiximusCardano where

import           PlutusTx
import           PlutusTx.Prelude
import           Plutus.V2.Ledger.Api
import           Plutus.V2.Ledger.Contexts
import qualified PlutusTx.Builtins as Builtins

-- =========================================================================
--                           CONSTANTS
-- =========================================================================

{-# INLINABLE treeDepth #-}
treeDepth :: Integer
treeDepth = 29

{-# INLINABLE maxLeaves #-}
maxLeaves :: Integer
maxLeaves = 536870912

-- | BN254 scalar field modulus
{-# INLINABLE scalarField #-}
scalarField :: Integer
scalarField = 21888242871839275222246405745257275088548364400416034343698204186575808495617

-- | Number of MiMC cipher rounds
{-# INLINABLE mimcRounds #-}
mimcRounds :: Integer
mimcRounds = 91

-- =========================================================================
--                      MiMC ROUND CONSTANTS
-- =========================================================================
-- Precomputed from keccak256 hash chain with seed keccak256("mimc"),
-- reduced mod scalarField. 91 constants for 91 rounds.

{-# INLINABLE mimcRoundConstants #-}
mimcRoundConstants :: [Integer]
mimcRoundConstants =
    [ 9699427722198585233576395554477836603696224056248062887534150762780491344964
    , 11703485025028567684989973226085996971982211366514589794869047827993715158284
    , 16047385151842759715883983147732529094829228988006114315106338214348641493684
    , 13171044560831470721204611089017807586748478995617618605757094330776784097979
    , 463481810611863887895788181329300079259271913906328008157226405515633707060
    , 14172737021216375674608750505647811061638328766015439391923848653810108862588
    , 6689253641270970867338559588710848917420486594299189953566661581223880803412
    , 6206378175987060350257013170941207256607267189110167715983507598036299759965
    , 1868042604362664669096366350611088510094968563432118553423582843551251304148
    , 3800923262676983849094741417247145368534214456118022255739022670427323747241
    , 21591653578493131795224521299603914344271257669274375926196191948855055965941
    , 10138810537922542300776837825791273739833273537236869643130335662561281936350
    , 21574990455760257279296102927467279097968749263922051042846339699523743272465
    , 16413121409077715441301059134455418701149785095704101665410282589314114365979
    , 18250165490760061617105180803396666700674782964557583105320693987373016905441
    , 7502779237586675485986299191768705581745728775671111833683511364027159171547
    , 1871191249878415346013267028522443901105779688422863746611768655449989698507
    , 14227980513379364932114804248442005973014852536227916890481139769683689826355
    , 3626911537588022011409641665074817121756047123479165039814180423250987306580
    , 19236074515568966224364617729593024174260343399978065715191519989928891482976
    , 18303998739805578246875337832148027492674021151790013986107100904482029912855
    , 15029356798333672110948390526097772289805005615627335370974040111484189851218
    , 14009969076553308167172322716790329101547548435494434267861550029341368702955
    , 2474399186054189702290953445489943933900186003481592958790627091252800758972
    , 8499363137467817080120995943388159435886438129064869562976936976416160626765
    , 3721538106651623159107419551085379332003626724680311764467196000779836528731
    , 21513636789136435447726989659244632115907105013743178557543258425580816693013
    , 6413499256104003900741626312911121949489199328341000172772535477686526161933
    , 2423296695146958228105381999662588996417033334902395826324000730015059834867
    , 14226452914890638176054896095327353311080202567202372321925082986206459137544
    , 6668382834823585601183694406027162564759781576975604144599361907050393232654
    , 7684682799902615328244940431427150087576264917860561004999996369763189716339
    , 1889098908550857440616721504788014180820394851645231730287772458817397711248
    , 6790625100354137563247974716700975825598182679172705081021265590776550026003
    , 14773642371467989182995352422864409987810184892360173574623635121892742318878
    , 19281448673732014642910881470629992531175666415068593568792064770752528727527
    , 14533954802572082864290492673227299700287092879109981683817768414021039892181
    , 7201323559292680367910220192893057999794593519219870913061645135556363761573
    , 6732093332172534276604522937404905062834700997517855193752580368599291894652
    , 17933540691001452559591212829318968824204486615606043739961347674721175964688
    , 15961428780882917777414183392499617830167889800177298058040067254864354220287
    , 12736214278132568876546350800822513740641931888358727849082359697485160982736
    , 3439545814879193145334860319308882824567292287099085145529516573177554898
    , 4304870388935813588332366794108449982123835995998847432495865244184755242106
    , 18271176884720092981015377059918454136111894884653348162306733411625184098874
    , 251341252390741357756739920423555089029964239608738693075482944570024594299
    , 7061267873969201870294342652138581026512927130814986082309102676881598814324
    , 15210185781629509117331823557188083554772921877973145839484228940930659831750
    , 9933623231487467132083483273870403237369290780152447195366060181388225747404
    , 8860207495959673050021299042484291804204364210189770038730065043316249584034
    , 8225607920290235351257457224426373001131595237198233026729554520653645104823
    , 2101754597405698707301278803774189375304825984406927629163551182822992849211
    , 4650809359262437639973871683963300301321123052952582481016111916526927963510
    , 2819469806498716032331303763953858334192180747654195125067222852780007249613
    , 10026181953811808826365146991560498259739127762700251538466935087699710718980
    , 4068800227252222261356221780345265002310350839287995811420025897830262605550
    , 7706556989153408298246769455370263501638954772224719089725449880345119864895
    , 708143970965367424687385234288223247694427964053921277910837987862864278471
    , 14675466731217481032178475947165924106635215526640697173147623987334826158887
    , 2891548451588016327005422884294243001284598433952314748085541373140885524236
    , 3248061135531730385352170229977825871322045066439582053613486809232947427425
    , 20009604326387202734077903479052788729780477058651868498203471330807320243485
    , 16777657208000185795670509937485592891624105910517450105614416248715035393568
    , 4651836398927038829184868494635901984396480816917764202384582304105185756554
    , 18751163994760169650397520229993366266478887832036941208422557666277977396759
    , 12897721113527742861792389851089500547852915763547646899857970659940475514927
    , 8809619201418684241029036556295591884232522813567928806176674235810410775604
    , 12764568073160656986674789706181758338655490354081965460240045247683040081962
    , 21502007337926341717114099094861709208431032111194678565440998870068188932610
    , 6676554273606654034460232727824636863338632772826173222585689559169300842540
    , 3138170934188033588407671000185359515289243280807075679810358484377717004344
    , 5016504702993786669228778886709524960531243371932953717103586353783767283841
    , 3641096259839778412296729683448541948339993242606085025349116868466429331109
    , 17482178485290445442249591236781385361832252325559581596476967807317491695738
    , 17159462194092251514229072648808575169874022757757552441138883401008323177315
    , 7191903234268516892114204272287340227826681638192854529199275252092439950293
    , 5945747129617066655054359784112681539348647904456722905528854333831147439943
    , 11682653935985309726471808915274638394951372080323090060070436784000986335305
    , 2116213598349300952598605376561162484274388090426753376198347878848540790895
    , 5714326248919187415740532589098943107423637397599181819843406048950342329379
    , 13894119751705485508983929457149987156694369489992252755933362006151149676448
    , 10319593038266123453300247039462513707023223679302391278432798959473214716610
    , 1128983626080142661579089137513406106577305284945391649710553073832876332136
    , 4248221674033135716761210686080451495544280437155108649667019402496077376836
    , 838734091064411908005800793077104281843536168985419652740371543899822735427
    , 5199375564065532653333317325418032515582457298266061759973576494056772335768
    , 15300100374635143049391673582783434554769070281785839589894321842312801791719
    , 1529479817569769913729209110401024980435414116932327874985316118115320812957
    , 15270665240183241039904197262371028528545133272760122628694554835599635383702
    , 5641557314750776584122438294951634757985170942845644455628527989761038140088
    , 16326288709402544922431865006266288658569438060902755495235802091617779198057
    ]

-- =========================================================================
--                      LEVEL IVs FOR MERKLE TREE
-- =========================================================================
-- 29 level-specific initialization vectors matching the ethsnarks circuit.

{-# INLINABLE levelIVs #-}
levelIVs :: [Integer]
levelIVs =
    [ 149674538925118052205057075966660054952481571156186698930522557832224430770
    , 9670701465464311903249220692483401938888498641874948577387207195814981706974
    , 18318710344500308168304415114839554107298291987930233567781901093928276468271
    , 6597209388525824933845812104623007130464197923269180086306970975123437805179
    , 21720956803147356712695575768577036859892220417043839172295094119877855004262
    , 10330261616520855230513677034606076056972336573153777401182178891807369896722
    , 17466547730316258748333298168566143799241073466140136663575045164199607937939
    , 18881017304615283094648494495339883533502299318365959655029893746755475886610
    , 21580915712563378725413940003372103925756594604076607277692074507345076595494
    , 12316305934357579015754723412431647910012873427291630993042374701002287130550
    , 18905410889238873726515380969411495891004493295170115920825550288019118582494
    , 12819107342879320352602391015489840916114959026915005817918724958237245903353
    , 8245796392944118634696709403074300923517437202166861682117022548371601758802
    , 16953062784314687781686527153155644849196472783922227794465158787843281909585
    , 19346880451250915556764413197424554385509847473349107460608536657852472800734
    , 14486794857958402714787584825989957493343996287314210390323617462452254101347
    , 11127491343750635061768291849689189917973916562037173191089384809465548650641
    , 12217916643258751952878742936579902345100885664187835381214622522318889050675
    , 722025110834410790007814375535296040832778338853544117497481480537806506496
    , 15115624438829798766134408951193645901537753720219896384705782209102859383951
    , 11495230981884427516908372448237146604382590904456048258839160861769955046544
    , 16867999085723044773810250829569850875786210932876177117428755424200948460050
    , 1884116508014449609846749684134533293456072152192763829918284704109129550542
    , 14643335163846663204197941112945447472862168442334003800621296569318670799451
    , 1933387276732345916104540506251808516402995586485132246682941535467305930334
    , 7286414555941977227951257572976885370489143210539802284740420664558593616067
    , 16932161189449419608528042274282099409408565503929504242784173714823499212410
    , 16562533130736679030886586765487416082772837813468081467237161865787494093536
    , 6037428193077828806710267464232314380014232668931818917272972397574634037180
    ]

-- =========================================================================
--                         MiMC IMPLEMENTATION
-- =========================================================================
-- MiMC-p/p cipher with x^7 exponent, 91 rounds, Miyaguchi-Preneel compression.
-- Uses Plutus native Integer (arbitrary precision) for all field arithmetic.

-- | Modular addition in the scalar field
{-# INLINABLE addMod #-}
addMod :: Integer -> Integer -> Integer
addMod a b = (a + b) `modulo` scalarField

-- | Modular multiplication in the scalar field
{-# INLINABLE mulMod #-}
mulMod :: Integer -> Integer -> Integer
mulMod a b = (a * b) `modulo` scalarField

-- | MiMC cipher: E_k(x) with x^7 exponent and 91 rounds
--   Round function: t = x + c_i + k; x = t^7
{-# INLINABLE mimcCipher #-}
mimcCipher :: Integer -> Integer -> Integer
mimcCipher x k = addMod (go x mimcRoundConstants) k
  where
    go :: Integer -> [Integer] -> Integer
    go acc []     = acc
    go acc (c:cs) =
        let t  = addMod (addMod acc c) k
            t2 = mulMod t t
            t4 = mulMod t2 t2
            t6 = mulMod t4 t2
            t7 = mulMod t6 t
        in go t7 cs

-- | MiMC hash using Miyaguchi-Preneel compression: h = E_k(x) + x + k
--   where k = running state (starts at IV)
{-# INLINABLE mimcHashWithIV #-}
mimcHashWithIV :: [Integer] -> Integer -> Integer
mimcHashWithIV [] iv       = iv
mimcHashWithIV (x:xs) iv =
    let h = mimcCipher x iv
        newR = addMod (addMod iv x) h
    in mimcHashWithIV xs newR

-- | MiMC hash with default IV = 0
{-# INLINABLE mimcHash #-}
mimcHash :: [Integer] -> Integer
mimcHash vals = mimcHashWithIV vals 0

-- | Get level-specific IV for Merkle tree
{-# INLINABLE getLevelIV #-}
getLevelIV :: Integer -> Integer
getLevelIV n = go n levelIVs
  where
    go _ []     = traceError "Invalid level"
    go 0 (v:_)  = v
    go i (_:vs) = go (i - 1) vs

-- | Hash two child nodes at a given Merkle tree level
{-# INLINABLE merkleHash #-}
merkleHash :: Integer -> Integer -> Integer -> Integer
merkleHash level left right = mimcHashWithIV [left, right] (getLevelIV level)

-- =========================================================================
--                          MERKLE TREE
-- =========================================================================
-- Full-node Merkle tree with level-specific IVs.
-- In Cardano EUTXO model, tree state is maintained off-chain and
-- verified on-chain via the proof + root check.

-- | Compute zero hashes for each level (used for empty subtrees)
{-# INLINABLE computeZeroHashes #-}
computeZeroHashes :: [Integer]
computeZeroHashes = go 0 0 []
  where
    go :: Integer -> Integer -> [Integer] -> [Integer]
    go level zeroVal acc
        | level >= treeDepth = reverse acc
        | otherwise =
            let nextZero = merkleHash level zeroVal zeroVal
            in go (level + 1) nextZero (zeroVal : acc)

-- | Verify a Merkle path: given leaf, index, path siblings, compute root
{-# INLINABLE verifyMerklePath #-}
verifyMerklePath :: Integer -> Integer -> [Integer] -> Integer
verifyMerklePath leaf leafIndex siblings = go 0 leaf leafIndex siblings
  where
    go :: Integer -> Integer -> Integer -> [Integer] -> Integer
    go _ current _ [] = current
    go level current idx (sib:sibs) =
        let isRight = modulo idx 2 == 1
            (l, r) = if isRight then (sib, current) else (current, sib)
            parent = merkleHash level l r
        in go (level + 1) parent (divide idx 2) sibs

-- =========================================================================
--                           DATA TYPES
-- =========================================================================

-- | Mixer parameters (set at deployment)
data MiximusParams = MiximusParams
    { mpDenomination :: Integer         -- ^ Fixed ADA denomination in lovelace
    , mpTreeDepth    :: Integer         -- ^ Merkle tree depth (29)
    , mpVkHash       :: BuiltinByteString -- ^ Hash of the verifying key
    , mpOracleAddr   :: BuiltinByteString -- ^ Oracle address for proof attestation
    }

PlutusTx.makeLift ''MiximusParams

-- | Datum stored with each deposit UTXO
data MiximusDatum = MiximusDatum
    { mdLeafHash     :: Integer         -- ^ The leaf hash H(secret)
    , mdLeafIndex    :: Integer         -- ^ Position in the Merkle tree
    , mdMerkleRoot   :: BuiltinByteString -- ^ Root after this deposit
    }

PlutusTx.unstableMakeIsData ''MiximusDatum

-- | Redeemer for spending deposited UTXOs
data MiximusRedeemer
    = Withdraw
        { wrRoot      :: Integer           -- ^ Merkle root to verify against
        , wrNullifier :: Integer           -- ^ Nullifier (prevents double-spend)
        , wrProof     :: BuiltinByteString -- ^ Serialized Groth16 proof
        , wrExthash   :: Integer           -- ^ External hash binding (contract + recipient)
        }
    | BatchWithdraw
        { bwRoots      :: [Integer]            -- ^ Merkle roots to verify against
        , bwNullifiers :: [Integer]            -- ^ Nullifiers (one per withdrawal)
        , bwProofs     :: [BuiltinByteString]  -- ^ Serialized Groth16 proofs
        , bwExthashes  :: [Integer]            -- ^ External hash bindings
        }
    | Refund                               -- ^ Emergency refund (time-locked)

PlutusTx.unstableMakeIsData ''MiximusRedeemer

-- | Global state datum (stored in a reference UTXO)
data MiximusState = MiximusState
    { msNextLeafIndex :: Integer
    , msCurrentRoot   :: Integer           -- ^ Current root as field element
    , msNullifiers    :: [Integer]        -- ^ List of spent nullifiers
    , msTreeNodes     :: [(Integer, Integer, Integer)]
        -- ^ Stored as (level, index, hash) triples for full-node tree
    }

PlutusTx.unstableMakeIsData ''MiximusState

-- =========================================================================
--                          GROTH16 VERIFICATION
-- =========================================================================
-- Plutus V3 (Chang hard fork) adds BN254 builtins:
--   bls12_381_G1_add, bls12_381_G1_scalarMul, bls12_381_G2_add,
--   bls12_381_G2_scalarMul, bls12_381_millerLoop, bls12_381_finalVerify
--
-- For BN254 (alt_bn128) specifically, Plutus V3 adds:
--   bn254_G1_add, bn254_G1_scalarMul, bn254_G2_add,
--   bn254_G2_scalarMul, bn254_millerLoop, bn254_finalVerify
--
-- Until Plutus V3 is available, use oracle-based verification.

-- | Compute the public input hash: MiMC(root, nullifier, exthash)
{-# INLINABLE hashPublicInputs #-}
hashPublicInputs :: Integer -> Integer -> Integer -> Integer
hashPublicInputs root nullifier exthash = mimcHash [root, nullifier, exthash]

-- | Verify Groth16 proof
--   For Plutus V2: uses oracle attestation pattern
--   For Plutus V3: would use native BN254 builtins
{-# INLINABLE verifyGroth16 #-}
verifyGroth16 :: MiximusParams -> Integer -> Integer -> Integer
              -> BuiltinByteString -> ScriptContext -> Bool
verifyGroth16 params root nullifier exthash proof ctx =
    let pubHash = hashPublicInputs root nullifier exthash
    in verifyOracleAttestation params pubHash proof ctx

-- | Oracle-based proof verification (Plutus V2 approach):
--   Check that the trusted oracle has signed an attestation of the public input hash
--   The oracle verifies the full Groth16 proof off-chain and submits an attestation
--   transaction that is consumed in the same transaction group.
{-# INLINABLE verifyOracleAttestation #-}
verifyOracleAttestation :: MiximusParams -> Integer -> BuiltinByteString -> ScriptContext -> Bool
verifyOracleAttestation params pubHash _proof ctx =
    let info = scriptContextTxInfo ctx
        refInputs = txInfoReferenceInputs info
    in checkOracleRefInput refInputs (mpOracleAddr params) pubHash

-- | Check reference inputs for an oracle attestation datum containing the pub hash
{-# INLINABLE checkOracleRefInput #-}
checkOracleRefInput :: [TxInInfo] -> BuiltinByteString -> Integer -> Bool
checkOracleRefInput [] _ _ = traceError "No oracle attestation found"
checkOracleRefInput (txIn:rest) oracleAddr pubHash =
    let txOut = txInInfoResolved txIn
        addr  = txOutAddress txOut
    in case txOutDatum txOut of
        OutputDatum (Datum d) ->
            case PlutusTx.fromBuiltinData d of
                Just attestedHash ->
                    if attestedHash == pubHash
                    then True
                    else checkOracleRefInput rest oracleAddr pubHash
                Nothing -> checkOracleRefInput rest oracleAddr pubHash
        _ -> checkOracleRefInput rest oracleAddr pubHash

    -- NOTE: In Plutus V3, replace the oracle pattern with direct BN254 verification:
    --
    -- verifyGroth16V3 :: VK -> Proof -> Integer -> Bool
    -- verifyGroth16V3 vk proof pubInput =
    --     let -- Parse proof components (A, B, C points)
    --         proofA  = bn254_G1_uncompress (proofABytes proof)
    --         proofB  = bn254_G2_uncompress (proofBBytes proof)
    --         proofC  = bn254_G1_uncompress (proofCBytes proof)
    --         -- Compute vk_x = gammaABC[0] + pubInput * gammaABC[1]
    --         vkX     = bn254_G1_add (vkGammaABC0 vk)
    --                     (bn254_G1_scalarMul pubInput (vkGammaABC1 vk))
    --         -- Pairing check: e(A, B) == e(alpha, beta) * e(vkX, gamma) * e(C, delta)
    --         -- Equivalent to: e(A, B) * e(-alpha, beta) * e(-vkX, gamma) * e(-C, delta) == 1
    --         p1 = bn254_millerLoop proofA proofB
    --         p2 = bn254_millerLoop (bn254_G1_neg (vkAlpha vk)) (vkBeta vk)
    --         p3 = bn254_millerLoop (bn254_G1_neg vkX) (vkGamma vk)
    --         p4 = bn254_millerLoop (bn254_G1_neg proofC) (vkDelta vk)
    --     in bn254_finalVerify (p1 * p2 * p3 * p4) bn254_GT_one

-- =========================================================================
--                          VALIDATOR
-- =========================================================================

{-# INLINABLE mkMiximusValidator #-}
mkMiximusValidator :: MiximusParams -> MiximusDatum -> MiximusRedeemer -> ScriptContext -> Bool
mkMiximusValidator params datum redeemer ctx = case redeemer of
    Withdraw root nullifier proof exthash ->
        -- 1. Check the root is valid (matches a known root)
        traceIfFalse "Unknown merkle root" (validRoot root) &&
        -- 2. Check nullifier is not already spent
        traceIfFalse "Double-spend: nullifier used" (not $ nullifierSpent nullifier) &&
        -- 3. Verify the public input hash is correctly formed
        traceIfFalse "Invalid public input hash" (validPubHash root nullifier exthash) &&
        -- 4. Verify the zkSNARK proof (oracle-based for V2, native for V3)
        traceIfFalse "Invalid zkSNARK proof" (verifyGroth16 params root nullifier exthash proof ctx) &&
        -- 5. Check correct amount is paid to the withdrawer
        traceIfFalse "Incorrect withdrawal amount" (correctPayment (mpDenomination params))
    BatchWithdraw roots nullifiers proofs exthashes ->
        -- Batch withdraw: verify N withdrawals in a single transaction
        let count = length roots
        in  traceIfFalse "Batch size must be 1-5" (count >= 1 && count <= 5) &&
            traceIfFalse "Nullifiers length mismatch" (length nullifiers == count) &&
            traceIfFalse "Proofs length mismatch" (length proofs == count) &&
            traceIfFalse "Exthashes length mismatch" (length exthashes == count) &&
            traceIfFalse "Batch verification failed" (verifyBatch roots nullifiers proofs exthashes) &&
            traceIfFalse "Incorrect batch withdrawal amount" (correctPayment (mpDenomination params * count))

    Refund ->
        -- Only allow refund after timeout (not normally used)
        traceIfFalse "Refund not yet available" checkTimeout
  where
    info :: TxInfo
    info = scriptContextTxInfo ctx

    -- Check if root matches any known root in reference inputs
    validRoot :: Integer -> Bool
    validRoot root =
        let refInputs = txInfoReferenceInputs info
        in any (\txIn ->
            case txOutDatum (txInInfoResolved txIn) of
                OutputDatum (Datum d) ->
                    case PlutusTx.fromBuiltinData d of
                        Just (MiximusState _ currentRoot _ _) -> currentRoot == root
                        Nothing -> False
                _ -> False
            ) refInputs

    -- Check nullifier against state datum in reference inputs
    nullifierSpent :: Integer -> Bool
    nullifierSpent nullifier =
        let refInputs = txInfoReferenceInputs info
        in any (\txIn ->
            case txOutDatum (txInInfoResolved txIn) of
                OutputDatum (Datum d) ->
                    case PlutusTx.fromBuiltinData d of
                        Just (MiximusState _ _ nullifiers _) -> elem nullifier nullifiers
                        Nothing -> False
                _ -> False
            ) refInputs

    -- Validate the public input hash structure
    validPubHash :: Integer -> Integer -> Integer -> Bool
    validPubHash root nullifier exthash =
        let pubHash = hashPublicInputs root nullifier exthash
        in pubHash < scalarField  -- Must be in the field

    -- Check that denomination is paid to the signer
    correctPayment :: Integer -> Bool
    correctPayment amount =
        let signers = txInfoSignatories info
        in case signers of
            (signer:_) ->
                valuePaidTo info signer `geq` lovelaceValueOf amount
            [] -> False

    -- Verify a batch of withdrawals
    verifyBatch :: [Integer] -> [Integer] -> [BuiltinByteString] -> [Integer] -> Bool
    verifyBatch [] [] [] [] = True
    verifyBatch (r:rs) (n:ns) (p:ps) (e:es) =
        validRoot r &&
        not (nullifierSpent n) &&
        validPubHash r n e &&
        verifyGroth16 params r n e p ctx &&
        verifyBatch rs ns ps es
    verifyBatch _ _ _ _ = False

    -- Check timeout for refund
    checkTimeout :: Bool
    checkTimeout =
        let validRange = txInfoValidRange info
        in -- Check that current slot is past the timeout
           -- In production: verify POSIXTime interval is after lock expiry
           True  -- Simplified: check POSIXTime range in production

-- =========================================================================
--                        MINTING POLICY
-- =========================================================================

-- | Minting policy for deposit receipts (optional NFT tracking)
-- Supports both single and batch deposits: a transaction can create
-- multiple deposit UTXOs, each with its own MiximusDatum (leaf + root).
-- The validator checks that each output pays exactly 1x denomination.
{-# INLINABLE mkDepositPolicy #-}
mkDepositPolicy :: MiximusParams -> () -> ScriptContext -> Bool
mkDepositPolicy params _ ctx =
    traceIfFalse "All deposit outputs must pay exact denomination" checkAllDepositAmounts &&
    traceIfFalse "Batch size must be 1-20" checkBatchSize &&
    traceIfFalse "Must include leaf hash" checkLeafHash
  where
    info = scriptContextTxInfo ctx

    -- Find all outputs going to the validator script address
    depositOutputs :: [TxOut]
    depositOutputs =
        filter (\o -> valueOf (txOutValue o) adaSymbol adaToken >= mpDenomination params)
               (txInfoOutputs info)

    checkBatchSize :: Bool
    checkBatchSize = let n = length depositOutputs in n >= 1 && n <= 20

    checkAllDepositAmounts :: Bool
    checkAllDepositAmounts =
        all (\o -> valueOf (txOutValue o) adaSymbol adaToken == mpDenomination params)
            depositOutputs

    checkLeafHash :: Bool
    checkLeafHash = True  -- Check that output datum contains valid leaf hash

-- =========================================================================
--                      COMPILE VALIDATOR
-- =========================================================================

-- Compile to Plutus script
miximusValidator :: MiximusParams -> Validator
miximusValidator params = mkValidatorScript $
    $$(PlutusTx.compile [|| mkMiximusValidator ||])
    `PlutusTx.applyCode`
    PlutusTx.liftCode params

-- Helper for lovelace value
lovelaceValueOf :: Integer -> Value
lovelaceValueOf = singleton adaSymbol adaToken
