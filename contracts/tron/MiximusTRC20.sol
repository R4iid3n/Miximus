// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.19;

/**
 * @title MiximusTRC20
 * @notice Mixer for TRC20 tokens on Tron network.
 *         Supports: USDT (TRC20), USDC (TRC20), TUSD (TRC20), WBTC (TRC20), etc.
 *
 * TRC20 is functionally identical to ERC20 but runs on Tron's TVM.
 * Deploy via TronBox: tronbox migrate --network mainnet
 */

interface ITRC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
    function balanceOf(address who) external view returns (uint256);
}

contract MiximusTRC20 {
    uint256 public constant TREE_DEPTH = 29;
    uint256 public constant MAX_LEAVES = 2**TREE_DEPTH;
    uint256 public constant MAX_BATCH_SIZE = 20;
    uint256 public constant SCALAR_FIELD =
        21888242871839275222246405745257275088548364400416034343698204186575808495617;

    ITRC20 public immutable token;
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

    event Deposit(uint256 indexed leafHash, uint256 indexed leafIndex, uint256 timestamp);
    event Withdrawal(address indexed recipient, uint256 nullifier, uint256 timestamp);

    constructor(
        address _token,
        uint256 _denomination,
        string memory _assetSymbol,
        uint256[14] memory _vk,
        uint256[] memory _vkGammaABC
    ) {
        require(_token != address(0), "Invalid token");
        require(_denomination > 0, "Denomination must be > 0");
        token = ITRC20(_token);
        denomination = _denomination;
        assetSymbol = _assetSymbol;
        vk = _vk;
        vkGammaABC = _vkGammaABC;
        _initMerkleTree();
    }

    function deposit(uint256 _leaf) external returns (uint256, uint256) {
        _safeTransferFrom(msg.sender, address(this), denomination);

        require(nextLeafIndex < MAX_LEAVES, "Tree full");
        uint256 leafIndex = nextLeafIndex++;
        uint256 newRoot = _insertLeaf(_leaf, leafIndex);
        currentRoot = newRoot;
        roots[newRoot] = true;

        emit Deposit(_leaf, leafIndex, block.timestamp);
        return (newRoot, leafIndex);
    }

    /**
     * @notice Batch deposit TRC20 tokens — deposit N units in a single transaction
     */
    function batchDeposit(uint256[] calldata _leaves) external returns (uint256 startIndex) {
        require(_leaves.length > 0 && _leaves.length <= MAX_BATCH_SIZE, "Invalid batch size");
        uint256 totalAmount = denomination * _leaves.length;
        _safeTransferFrom(msg.sender, address(this), totalAmount);

        startIndex = nextLeafIndex;
        for (uint256 i = 0; i < _leaves.length; i++) {
            require(nextLeafIndex < MAX_LEAVES, "Tree full");
            uint256 leafIndex = nextLeafIndex++;
            uint256 newRoot = _insertLeaf(_leaves[i], leafIndex);
            currentRoot = newRoot;
            roots[newRoot] = true;
            emit Deposit(_leaves[i], leafIndex, block.timestamp);
        }
    }

    function withdraw(uint256 _root, uint256 _nullifier, uint256[8] memory _proof) external {
        require(!nullifiers[_nullifier], "Double-spend");
        require(roots[_root], "Unknown root");

        uint256 exthash = uint256(sha256(abi.encodePacked(address(this), msg.sender))) % SCALAR_FIELD;
        require(_verifyProof(_root, _nullifier, exthash, _proof), "Invalid proof");

        nullifiers[_nullifier] = true;
        _safeTransfer(msg.sender, denomination);
        emit Withdrawal(msg.sender, _nullifier, block.timestamp);
    }

    /**
     * @notice Batch withdraw TRC20 tokens — process up to 5 withdrawals in a single transaction
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
            require(!nullifiers[_nullifiers[i]], "Double-spend");
            require(roots[_roots[i]], "Unknown root");

            uint256 exthash = uint256(sha256(abi.encodePacked(address(this), msg.sender))) % SCALAR_FIELD;
            require(_verifyProof(_roots[i], _nullifiers[i], exthash, _proofs[i]), "Invalid proof");
            nullifiers[_nullifiers[i]] = true;
            emit Withdrawal(msg.sender, _nullifiers[i], block.timestamp);
        }

        // Transfer total amount in a single transfer
        _safeTransfer(msg.sender, denomination * count);
    }

    function withdrawViaRelayer(
        uint256 _root, uint256 _nullifier, uint256[8] memory _proof,
        address _recipient, uint256 _relayerFee
    ) external {
        require(_relayerFee < denomination, "Fee too high");
        require(!nullifiers[_nullifier], "Double-spend");
        require(roots[_root], "Unknown root");

        uint256 exthash = uint256(sha256(abi.encodePacked(address(this), _recipient))) % SCALAR_FIELD;
        require(_verifyProof(_root, _nullifier, exthash, _proof), "Invalid proof");

        nullifiers[_nullifier] = true;
        if (_relayerFee > 0) _safeTransfer(msg.sender, _relayerFee);
        _safeTransfer(_recipient, denomination - _relayerFee);
        emit Withdrawal(_recipient, _nullifier, block.timestamp);
    }

    function _safeTransfer(address _to, uint256 _amount) internal {
        (bool success, bytes memory data) = address(token).call(
            abi.encodeWithSelector(ITRC20.transfer.selector, _to, _amount)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "Transfer failed");
    }

    function _safeTransferFrom(address _from, address _to, uint256 _amount) internal {
        (bool success, bytes memory data) = address(token).call(
            abi.encodeWithSelector(ITRC20.transferFrom.selector, _from, _to, _amount)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "TransferFrom failed");
    }

    // =========================================================================
    //                     VIEW FUNCTIONS
    // =========================================================================

    function getRoot() public view returns (uint256) { return currentRoot; }
    function isSpent(uint256 n) public view returns (bool) { return nullifiers[n]; }

    function getExtHash() public view returns (uint256) {
        return uint256(sha256(abi.encodePacked(address(this), msg.sender))) % SCALAR_FIELD;
    }

    function makeLeafHash(uint256 s) public pure returns (uint256) {
        uint256[] memory v = new uint256[](1); v[0] = s; return mimcHash(v);
    }

    function hashPublicInputs(uint256 r, uint256 n, uint256 e) public pure returns (uint256) {
        uint256[] memory v = new uint256[](3); v[0] = r; v[1] = n; v[2] = e;
        return mimcHash(v);
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

    // =========================================================================
    //                     zkSNARK VERIFICATION (Groth16 BN254)
    // =========================================================================

    function _verifyProof(uint256 r, uint256 n, uint256 e, uint256[8] memory p)
        internal view returns (bool)
    {
        uint256[] memory input = new uint256[](1);
        input[0] = hashPublicInputs(r, n, e);
        return _groth16Verify(vk, vkGammaABC, p, input);
    }

    function _groth16Verify(
        uint256[14] memory _vk, uint256[] memory _abc,
        uint256[8] memory _p, uint256[] memory _in
    ) internal view returns (bool) {
        require(_in.length + 1 == _abc.length / 2, "Length mismatch");
        uint256[2] memory vkX; vkX[0] = _abc[0]; vkX[1] = _abc[1];
        for (uint256 i = 0; i < _in.length; i++) {
            (uint256 mx, uint256 my) = _ecMul(_abc[2*(i+1)], _abc[2*(i+1)+1], _in[i]);
            (vkX[0], vkX[1]) = _ecAdd(vkX[0], vkX[1], mx, my);
        }
        return _pairingCheck(
            _p[0], _p[1],
            _p[2], _p[3], _p[4], _p[5],
            _p[6], _p[7],
            _vk, vkX[0], vkX[1]
        );
    }

    function _ecAdd(uint256 x1, uint256 y1, uint256 x2, uint256 y2)
        internal view returns (uint256, uint256) {
        uint256[4] memory i; i[0]=x1; i[1]=y1; i[2]=x2; i[3]=y2;
        uint256[2] memory r; bool s;
        assembly { s := staticcall(gas(), 0x06, i, 0x80, r, 0x40) }
        require(s); return (r[0], r[1]);
    }

    function _ecMul(uint256 x, uint256 y, uint256 sc)
        internal view returns (uint256, uint256) {
        uint256[3] memory i; i[0]=x; i[1]=y; i[2]=sc;
        uint256[2] memory r; bool s;
        assembly { s := staticcall(gas(), 0x07, i, 0x60, r, 0x40) }
        require(s); return (r[0], r[1]);
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

        uint256[1] memory result; bool success;
        assembly { success := staticcall(gas(), 0x08, input, 0x300, result, 0x20) }
        require(success, "Pairing failed");
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
            uint256[] memory v = new uint256[](2);
            v[0] = zero; v[1] = zero;
            zero = _mimcHashWithIV(v, _levelIV(i));
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
}
