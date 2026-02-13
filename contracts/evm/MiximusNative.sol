// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.19;

import "./MiximusBase.sol";

/**
 * @title MiximusNative
 * @notice Mixer for native currency on any EVM chain (ETH, BNB, AVAX, MATIC, CRO, GLMR, etc.)
 *         Users deposit a fixed denomination of native currency and withdraw using a zkSNARK proof.
 *
 * Supported chains: Ethereum, BSC, Polygon, Avalanche, Arbitrum, Base, Cronos,
 *                   Moonbeam, Ethereum Classic, Qtum, VeChain, Optimism
 */
contract MiximusNative is MiximusBase {

    constructor(
        uint256 _denomination,
        string memory _assetSymbol,
        uint256[14] memory _vk,
        uint256[] memory _vkGammaABC
    ) MiximusBase(_denomination, _assetSymbol, _vk, _vkGammaABC) {}

    /**
     * @notice Deposit native currency into the mixer
     * @param _leaf The leaf hash (H(secret)) to insert into the Merkle tree
     * @return newRoot The new Merkle root after insertion
     * @return leafIndex The index of the inserted leaf
     */
    function deposit(uint256 _leaf)
        external payable
        returns (uint256 newRoot, uint256 leafIndex)
    {
        require(msg.value == denomination, "Must deposit exact denomination");
        return _processDeposit(_leaf);
    }

    /**
     * @notice Batch deposit native currency — deposit N units in a single transaction
     * @param _leaves Array of leaf hashes to insert into the Merkle tree
     * @return startIndex The leaf index of the first inserted leaf
     */
    function batchDeposit(uint256[] calldata _leaves)
        external payable
        returns (uint256 startIndex)
    {
        require(msg.value == denomination * _leaves.length, "Wrong total value");
        startIndex = _processBatchDeposit(_leaves);
    }

    /**
     * @notice Withdraw native currency from the mixer by providing a valid zkSNARK proof
     * @param _root A known Merkle root to verify against
     * @param _nullifier The nullifier to prevent double-spending
     * @param _proof The Groth16 proof [A.x, A.y, B.x1, B.y1, B.x2, B.y2, C.x, C.y]
     */
    function withdraw(
        uint256 _root,
        uint256 _nullifier,
        uint256[8] memory _proof
    ) external {
        address payable recipient = payable(msg.sender);
        _processWithdraw(_root, _nullifier, _proof, recipient);
        _transferNative(recipient);
    }

    /**
     * @notice Batch withdraw native currency — withdraw N deposits in a single transaction
     * @param _roots Array of Merkle roots
     * @param _nullifiers Array of nullifiers
     * @param _proofs Array of Groth16 proofs
     */
    function batchWithdraw(
        uint256[] calldata _roots,
        uint256[] calldata _nullifiers,
        uint256[8][] calldata _proofs
    ) external {
        address payable recipient = payable(msg.sender);
        _processBatchWithdraw(_roots, _nullifiers, _proofs, recipient);
        uint256 totalAmount = denomination * _roots.length;
        (bool success, ) = recipient.call{value: totalAmount}("");
        require(success, "Native batch transfer failed");
    }

    /**
     * @notice Withdraw to a different address (relayer support)
     * @param _root A known Merkle root
     * @param _nullifier The nullifier
     * @param _proof The zkSNARK proof
     * @param _recipient The address to receive the native currency
     * @param _relayerFee Fee to pay the relayer (subtracted from denomination)
     */
    function withdrawViaRelayer(
        uint256 _root,
        uint256 _nullifier,
        uint256[8] memory _proof,
        address payable _recipient,
        uint256 _relayerFee
    ) external {
        require(_relayerFee < denomination, "Fee exceeds denomination");
        _processWithdraw(_root, _nullifier, _proof, _recipient);

        // Pay relayer fee
        if (_relayerFee > 0) {
            (bool relayerSuccess, ) = payable(msg.sender).call{value: _relayerFee}("");
            require(relayerSuccess, "Relayer fee transfer failed");
        }

        // Send remainder to recipient
        uint256 remaining = denomination - _relayerFee;
        (bool success, ) = _recipient.call{value: remaining}("");
        require(success, "Native transfer failed");
    }

    function _transferNative(address payable _to) internal {
        (bool success, ) = _to.call{value: denomination}("");
        require(success, "Native transfer failed");
    }

    /// @notice Emergency: contract should not hold excess funds, but just in case
    receive() external payable {}
}
