// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.19;

/**
 * @title MiximusNativeTron
 * @notice Mixer for TRX (native Tron currency).
 *         Tron uses TVM which is very similar to EVM but has differences:
 *         - address type is 21 bytes (not 20)
 *         - energy/bandwidth model instead of gas
 *         - BN254 precompiles at same addresses as EVM (0x06, 0x07, 0x08)
 *         - Uses `transferTo` instead of `.call{value:}`
 *
 * This contract is deployed via TronBox/TronIDE.
 * The zkSNARK circuit and proof generation remain identical to EVM.
 *
 * Supported assets: TRX (native)
 */
contract MiximusNativeTron {
    // =========================================================================
    //                              CONSTANTS
    // =========================================================================

    uint256 public constant TREE_DEPTH = 29;
    uint256 public constant MAX_LEAVES = 2**TREE_DEPTH;
    uint256 public constant MAX_BATCH_SIZE = 20;
    uint256 public constant SCALAR_FIELD =
        21888242871839275222246405745257275088548364400416034343698204186575808495617;

    // =========================================================================
    //                            STATE VARIABLES
    // =========================================================================

    uint256 public immutable denomination;
    string public assetSymbol;

    mapping(uint256 => bool) public nullifiers;
    mapping(uint256 => bool) public roots;
    uint256 public nextLeafIndex;

    // Merkle tree: full node storage (matching ethsnarks C++ circuit)
    mapping(uint256 => mapping(uint256 => uint256)) internal treeNodes;
    uint256[TREE_DEPTH] internal zeroHashes;
    uint256 internal currentRoot;

    uint256[14] internal vk;
    uint256[] internal vkGammaABC;

    address public owner;

    // =========================================================================
    //                               EVENTS
    // =========================================================================

    event Deposit(uint256 indexed leafHash, uint256 indexed leafIndex, uint256 timestamp);
    event Withdrawal(address indexed recipient, uint256 nullifier, uint256 timestamp);

    // =========================================================================
    //                            CONSTRUCTOR
    // =========================================================================

    constructor(
        uint256 _denomination,
        string memory _assetSymbol,
        uint256[14] memory _vk,
        uint256[] memory _vkGammaABC
    ) {
        require(_denomination > 0, "Denomination must be > 0");
        denomination = _denomination;
        assetSymbol = _assetSymbol;
        vk = _vk;
        vkGammaABC = _vkGammaABC;
        owner = msg.sender;
        _initMerkleTree();
    }

    // =========================================================================
    //                           PUBLIC FUNCTIONS
    // =========================================================================

    function getRoot() public view returns (uint256) {
        return currentRoot;
    }

    function isSpent(uint256 _nullifier) public view returns (bool) {
        return nullifiers[_nullifier];
    }

    function getExtHash() public view returns (uint256) {
        return uint256(sha256(abi.encodePacked(address(this), msg.sender))) % SCALAR_FIELD;
    }

    function makeLeafHash(uint256 _secret) public pure returns (uint256) {
        uint256[] memory vals = new uint256[](1);
        vals[0] = _secret;
        return mimcHash(vals);
    }

    function hashPublicInputs(
        uint256 _root, uint256 _nullifier, uint256 _exthash
    ) public pure returns (uint256) {
        uint256[] memory inputs = new uint256[](3);
        inputs[0] = _root;
        inputs[1] = _nullifier;
        inputs[2] = _exthash;
        return mimcHash(inputs);
    }

    function getPath(uint256 _leafIndex)
        public view returns (uint256[TREE_DEPTH] memory path, bool[TREE_DEPTH] memory addressBits)
    {
        require(_leafIndex < nextLeafIndex, "Leaf not yet inserted");
        for (uint256 i = 0; i < TREE_DEPTH; i++) {
            uint256 nodeIdx = _leafIndex >> i;
            addressBits[i] = nodeIdx & 1 == 1;
            uint256 siblingIdx = nodeIdx ^ 1;
            path[i] = _getNode(i, siblingIdx);
        }
    }

    /**
     * @notice Deposit TRX into the mixer
     */
    function deposit(uint256 _leaf) external payable returns (uint256, uint256) {
        require(msg.value == denomination, "Must deposit exact denomination");
        require(nextLeafIndex < MAX_LEAVES, "Merkle tree is full");

        uint256 leafIndex = nextLeafIndex;
        nextLeafIndex++;

        uint256 newRoot = _insertLeaf(_leaf, leafIndex);
        currentRoot = newRoot;
        roots[newRoot] = true;

        emit Deposit(_leaf, leafIndex, block.timestamp);
        return (newRoot, leafIndex);
    }

    /**
     * @notice Batch deposit TRX — deposit N units in a single transaction
     */
    function batchDeposit(uint256[] calldata _leaves) external payable returns (uint256 startIndex) {
        require(_leaves.length > 0 && _leaves.length <= MAX_BATCH_SIZE, "Invalid batch size");
        require(msg.value == denomination * _leaves.length, "Wrong total value");

        startIndex = nextLeafIndex;
        for (uint256 i = 0; i < _leaves.length; i++) {
            require(nextLeafIndex < MAX_LEAVES, "Merkle tree is full");
            uint256 leafIndex = nextLeafIndex;
            nextLeafIndex++;
            uint256 newRoot = _insertLeaf(_leaves[i], leafIndex);
            currentRoot = newRoot;
            roots[newRoot] = true;
            emit Deposit(_leaves[i], leafIndex, block.timestamp);
        }
    }

    /**
     * @notice Withdraw TRX using zkSNARK proof
     */
    function withdraw(
        uint256 _root,
        uint256 _nullifier,
        uint256[8] memory _proof
    ) external {
        require(!nullifiers[_nullifier], "Cannot double-spend");
        require(roots[_root], "Unknown merkle root");

        uint256 exthash = uint256(sha256(
            abi.encodePacked(address(this), msg.sender)
        )) % SCALAR_FIELD;

        require(_verifyProof(_root, _nullifier, exthash, _proof), "Invalid proof");

        nullifiers[_nullifier] = true;

        // Tron native transfer
        payable(msg.sender).transfer(denomination);

        emit Withdrawal(msg.sender, _nullifier, block.timestamp);
    }

    /**
     * @notice Batch withdraw TRX — process up to 5 withdrawals in a single transaction
     */
    function batchWithdraw(
        uint256[] calldata _roots,
        uint256[] calldata _nullifiers,
        uint256[8][] calldata _proofs
    ) external {
        uint256 count = _roots.length;
        require(count > 0 && count <= 5, "Batch size must be 1-5");
        require(_nullifiers.length == count, "Nullifiers length mismatch");
        require(_proofs.length == count, "Proofs length mismatch");

        for (uint256 i = 0; i < count; i++) {
            require(!nullifiers[_nullifiers[i]], "Cannot double-spend");
            require(roots[_roots[i]], "Unknown merkle root");

            uint256 exthash = uint256(sha256(
                abi.encodePacked(address(this), msg.sender)
            )) % SCALAR_FIELD;

            require(_verifyProof(_roots[i], _nullifiers[i], exthash, _proofs[i]), "Invalid proof");
            nullifiers[_nullifiers[i]] = true;
            emit Withdrawal(msg.sender, _nullifiers[i], block.timestamp);
        }

        // Transfer total amount in a single transfer
        payable(msg.sender).transfer(denomination * count);
    }

    /**
     * @notice Withdraw to a different address (relayer support)
     */
    function withdrawViaRelayer(
        uint256 _root,
        uint256 _nullifier,
        uint256[8] memory _proof,
        address payable _recipient,
        uint256 _relayerFee
    ) external {
        require(_relayerFee < denomination, "Fee exceeds denomination");
        require(!nullifiers[_nullifier], "Cannot double-spend");
        require(roots[_root], "Unknown merkle root");

        uint256 exthash = uint256(sha256(
            abi.encodePacked(address(this), _recipient)
        )) % SCALAR_FIELD;

        require(_verifyProof(_root, _nullifier, exthash, _proof), "Invalid proof");

        nullifiers[_nullifier] = true;

        if (_relayerFee > 0) {
            payable(msg.sender).transfer(_relayerFee);
        }
        _recipient.transfer(denomination - _relayerFee);

        emit Withdrawal(_recipient, _nullifier, block.timestamp);
    }

    // =========================================================================
    //                     zkSNARK VERIFICATION (Groth16 BN254)
    // =========================================================================

    function _verifyProof(
        uint256 _root,
        uint256 _nullifier,
        uint256 _exthash,
        uint256[8] memory _proof
    ) internal view returns (bool) {
        uint256[] memory input = new uint256[](1);
        input[0] = hashPublicInputs(_root, _nullifier, _exthash);
        return _groth16Verify(vk, vkGammaABC, _proof, input);
    }

    function _groth16Verify(
        uint256[14] memory _vk,
        uint256[] memory _vkGammaABC,
        uint256[8] memory _proof,
        uint256[] memory _input
    ) internal view returns (bool) {
        require(_input.length + 1 == _vkGammaABC.length / 2, "Input length mismatch");

        uint256[2] memory vkX;
        vkX[0] = _vkGammaABC[0];
        vkX[1] = _vkGammaABC[1];

        for (uint256 i = 0; i < _input.length; i++) {
            (uint256 mx, uint256 my) = _ecMul(
                _vkGammaABC[2 * (i + 1)],
                _vkGammaABC[2 * (i + 1) + 1],
                _input[i]
            );
            (vkX[0], vkX[1]) = _ecAdd(vkX[0], vkX[1], mx, my);
        }

        return _pairingCheck(
            _proof[0], _proof[1],
            _proof[2], _proof[3], _proof[4], _proof[5],
            _proof[6], _proof[7],
            _vk, vkX[0], vkX[1]
        );
    }

    // Tron TVM supports BN254 precompiles at the same addresses as EVM
    function _ecAdd(uint256 x1, uint256 y1, uint256 x2, uint256 y2)
        internal view returns (uint256 x, uint256 y)
    {
        uint256[4] memory input;
        input[0] = x1; input[1] = y1; input[2] = x2; input[3] = y2;
        uint256[2] memory result;
        bool success;
        assembly { success := staticcall(gas(), 0x06, input, 0x80, result, 0x40) }
        require(success, "EC add failed");
        return (result[0], result[1]);
    }

    function _ecMul(uint256 x, uint256 y, uint256 s)
        internal view returns (uint256 rx, uint256 ry)
    {
        uint256[3] memory input;
        input[0] = x; input[1] = y; input[2] = s;
        uint256[2] memory result;
        bool success;
        assembly { success := staticcall(gas(), 0x07, input, 0x60, result, 0x40) }
        require(success, "EC mul failed");
        return (result[0], result[1]);
    }

    function _pairingCheck(
        uint256 aX, uint256 aY,
        uint256 bX1, uint256 bY1, uint256 bX2, uint256 bY2,
        uint256 cX, uint256 cY,
        uint256[14] memory _vk,
        uint256 vkxX, uint256 vkxY
    ) internal view returns (bool) {
        uint256 P = 21888242871839275222246405745257275088696311157297823662689037894645226208583;
        uint256[24] memory input;

        // Pair 1: (A, B) — G2 coords in EVM order from ethsnarks export
        input[0] = aX;    input[1] = aY;
        input[2] = bX1;   input[3] = bY1;
        input[4] = bX2;   input[5] = bY2;

        // Pair 2: (-alpha, beta)
        input[6] = _vk[0];  input[7] = P - _vk[1];
        input[8] = _vk[2];  input[9] = _vk[3];
        input[10] = _vk[4]; input[11] = _vk[5];

        // Pair 3: (-vkX, gamma)
        input[12] = vkxX;   input[13] = P - vkxY;
        input[14] = _vk[6]; input[15] = _vk[7];
        input[16] = _vk[8]; input[17] = _vk[9];

        // Pair 4: (-C, delta)
        input[18] = cX;      input[19] = P - cY;
        input[20] = _vk[10]; input[21] = _vk[11];
        input[22] = _vk[12]; input[23] = _vk[13];

        uint256[1] memory result;
        bool success;
        assembly { success := staticcall(gas(), 0x08, input, 0x300, result, 0x20) }
        require(success, "Pairing check failed");
        return result[0] == 1;
    }

    // =========================================================================
    //                           MiMC HASH FUNCTION
    // =========================================================================

    function _mimcCipher(uint256 in_x, uint256 in_k) internal pure returns (uint256 out_x) {
        uint256 seed = uint256(keccak256(abi.encodePacked("mimc")));
        assembly {
            let c := mload(0x40)
            mstore(0x40, add(c, 32))
            mstore(c, seed)

            let localQ := 0x30644e72e131a029b85045b68181585d2833e84879b9709143e1f593f0000001
            let t
            let a

            for { let i := 91 } gt(i, 0) { i := sub(i, 1) } {
                mstore(c, keccak256(c, 32))
                t := addmod(addmod(in_x, mload(c), localQ), in_k, localQ)
                a := mulmod(t, t, localQ)
                in_x := mulmod(mulmod(a, mulmod(a, a, localQ), localQ), t, localQ)
            }

            out_x := addmod(in_x, in_k, localQ)
        }
    }

    function mimcHash(uint256[] memory _data) public pure returns (uint256) {
        return _mimcHashWithIV(_data, 0);
    }

    function _mimcHashWithIV(uint256[] memory _data, uint256 _iv) internal pure returns (uint256) {
        uint256 r = _iv;
        for (uint256 i = 0; i < _data.length; i++) {
            uint256 x = _data[i];
            uint256 h = _mimcCipher(x, r);
            r = addmod(addmod(r, x, SCALAR_FIELD), h, SCALAR_FIELD);
        }
        return r;
    }

    function _levelIV(uint256 _level) internal pure returns (uint256) {
        if (_level == 0) return 149674538925118052205057075966660054952481571156186698930522557832224430770;
        if (_level == 1) return 9670701465464311903249220692483401938888498641874948577387207195814981706974;
        if (_level == 2) return 18318710344500308168304415114839554107298291987930233567781901093928276468271;
        if (_level == 3) return 6597209388525824933845812104623007130464197923269180086306970975123437805179;
        if (_level == 4) return 21720956803147356712695575768577036859892220417043839172295094119877855004262;
        if (_level == 5) return 10330261616520855230513677034606076056972336573153777401182178891807369896722;
        if (_level == 6) return 17466547730316258748333298168566143799241073466140136663575045164199607937939;
        if (_level == 7) return 18881017304615283094648494495339883533502299318365959655029893746755475886610;
        if (_level == 8) return 21580915712563378725413940003372103925756594604076607277692074507345076595494;
        if (_level == 9) return 12316305934357579015754723412431647910012873427291630993042374701002287130550;
        if (_level == 10) return 18905410889238873726515380969411495891004493295170115920825550288019118582494;
        if (_level == 11) return 12819107342879320352602391015489840916114959026915005817918724958237245903353;
        if (_level == 12) return 8245796392944118634696709403074300923517437202166861682117022548371601758802;
        if (_level == 13) return 16953062784314687781686527153155644849196472783922227794465158787843281909585;
        if (_level == 14) return 19346880451250915556764413197424554385509847473349107460608536657852472800734;
        if (_level == 15) return 14486794857958402714787584825989957493343996287314210390323617462452254101347;
        if (_level == 16) return 11127491343750635061768291849689189917973916562037173191089384809465548650641;
        if (_level == 17) return 12217916643258751952878742936579902345100885664187835381214622522318889050675;
        if (_level == 18) return 722025110834410790007814375535296040832778338853544117497481480537806506496;
        if (_level == 19) return 15115624438829798766134408951193645901537753720219896384705782209102859383951;
        if (_level == 20) return 11495230981884427516908372448237146604382590904456048258839160861769955046544;
        if (_level == 21) return 16867999085723044773810250829569850875786210932876177117428755424200948460050;
        if (_level == 22) return 1884116508014449609846749684134533293456072152192763829918284704109129550542;
        if (_level == 23) return 14643335163846663204197941112945447472862168442334003800621296569318670799451;
        if (_level == 24) return 1933387276732345916104540506251808516402995586485132246682941535467305930334;
        if (_level == 25) return 7286414555941977227951257572976885370489143210539802284740420664558593616067;
        if (_level == 26) return 16932161189449419608528042274282099409408565503929504242784173714823499212410;
        if (_level == 27) return 16562533130736679030886586765487416082772837813468081467237161865787494093536;
        if (_level == 28) return 6037428193077828806710267464232314380014232668931818917272972397574634037180;
        revert("Invalid level");
    }

    // =========================================================================
    //                         MERKLE TREE
    // =========================================================================

    function _initMerkleTree() internal {
        uint256 zero = 0;
        for (uint256 i = 0; i < TREE_DEPTH; i++) {
            zeroHashes[i] = zero;
            uint256[] memory vals = new uint256[](2);
            vals[0] = zero;
            vals[1] = zero;
            zero = _mimcHashWithIV(vals, _levelIV(i));
        }
        currentRoot = zero;
        roots[currentRoot] = true;
    }

    function _insertLeaf(uint256 _leaf, uint256 _index) internal returns (uint256) {
        treeNodes[0][_index] = _leaf;
        uint256 currentNode = _leaf;
        uint256 idx = _index;

        for (uint256 level = 0; level < TREE_DEPTH; level++) {
            uint256 parentIdx = idx / 2;
            uint256[] memory vals = new uint256[](2);

            if (idx % 2 == 0) {
                vals[0] = currentNode;
                vals[1] = _getNode(level, idx + 1);
            } else {
                vals[0] = _getNode(level, idx - 1);
                vals[1] = currentNode;
            }

            currentNode = _mimcHashWithIV(vals, _levelIV(level));
            treeNodes[level + 1][parentIdx] = currentNode;
            idx = parentIdx;
        }

        return currentNode;
    }

    function _getNode(uint256 _level, uint256 _index) internal view returns (uint256) {
        uint256 val = treeNodes[_level][_index];
        if (val != 0) return val;
        return zeroHashes[_level];
    }

    receive() external payable {}
}
