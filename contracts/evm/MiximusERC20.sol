// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.19;

import "./MiximusBase.sol";

/**
 * @title IERC20Minimal
 * @notice Minimal ERC20 interface for token transfers
 */
interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
}

/**
 * @title MiximusERC20
 * @notice Mixer for ERC20 / BEP20 / any EVM-standard tokens.
 *         Works on ALL EVM chains for all token standards that implement transfer/transferFrom.
 *
 * Supported tokens include:
 *   - ERC20 (Ethereum): USDT, USDC, DAI, LINK, UNI, SHIB, MANA, ENJ, HOT, OMG, WBTC, WETH, etc.
 *   - BEP20 (BSC): USDT, USDC, DAI, BUSD, CAKE, LINK, UNI, YFI, SHIB, WBNB, BTT, etc.
 *   - Polygon tokens: USDT, USDC, DAI, WBTC, LINK, etc.
 *   - Avalanche C-Chain: USDT, USDC, DAI, WBTC, etc.
 *   - Any EVM-compatible token on Arbitrum, Base, Cronos, Moonbeam, etc.
 */
contract MiximusERC20 is MiximusBase {

    /// @notice The ERC20 token contract address
    IERC20 public immutable token;

    /// @notice Track token balance for safety
    uint256 public totalDeposited;

    constructor(
        address _token,
        uint256 _denomination,
        string memory _assetSymbol,
        uint256[14] memory _vk,
        uint256[] memory _vkGammaABC
    ) MiximusBase(_denomination, _assetSymbol, _vk, _vkGammaABC) {
        require(_token != address(0), "Invalid token address");
        token = IERC20(_token);
    }

    /**
     * @notice Deposit tokens into the mixer.
     *         Caller must have approved this contract to spend `denomination` tokens.
     * @param _leaf The leaf hash (H(secret)) to insert into the Merkle tree
     * @return newRoot The new Merkle root after insertion
     * @return leafIndex The index of the inserted leaf
     */
    function deposit(uint256 _leaf)
        external
        returns (uint256 newRoot, uint256 leafIndex)
    {
        // Pull tokens from depositor (requires prior approval)
        _safeTransferFrom(msg.sender, address(this), denomination);
        totalDeposited += denomination;

        return _processDeposit(_leaf);
    }

    /**
     * @notice Batch deposit tokens — deposit N units in a single transaction.
     *         Caller must have approved this contract to spend `denomination * N` tokens.
     * @param _leaves Array of leaf hashes to insert into the Merkle tree
     * @return startIndex The leaf index of the first inserted leaf
     */
    function batchDeposit(uint256[] calldata _leaves)
        external
        returns (uint256 startIndex)
    {
        uint256 totalAmount = denomination * _leaves.length;
        _safeTransferFrom(msg.sender, address(this), totalAmount);
        totalDeposited += totalAmount;
        startIndex = _processBatchDeposit(_leaves);
    }

    /**
     * @notice Withdraw tokens from the mixer by providing a valid zkSNARK proof
     * @param _root A known Merkle root
     * @param _nullifier The nullifier to prevent double-spending
     * @param _proof The Groth16 proof
     */
    function withdraw(
        uint256 _root,
        uint256 _nullifier,
        uint256[8] memory _proof
    ) external {
        address payable recipient = payable(msg.sender);
        _processWithdraw(_root, _nullifier, _proof, recipient);
        _safeTransfer(recipient, denomination);
        totalDeposited -= denomination;
    }

    /**
     * @notice Batch withdraw tokens — withdraw N deposits in a single transaction
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
        _safeTransfer(recipient, totalAmount);
        totalDeposited -= totalAmount;
    }

    /**
     * @notice Withdraw to a different address with relayer support
     * @param _root A known Merkle root
     * @param _nullifier The nullifier
     * @param _proof The zkSNARK proof
     * @param _recipient The address to receive the tokens
     * @param _relayerFee Fee paid to the relayer in tokens
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

        if (_relayerFee > 0) {
            _safeTransfer(msg.sender, _relayerFee);
        }

        uint256 remaining = denomination - _relayerFee;
        _safeTransfer(_recipient, remaining);
        totalDeposited -= denomination;
    }

    // =========================================================================
    //                     SAFE TOKEN TRANSFER HELPERS
    // =========================================================================

    /// @dev Handles tokens that don't return bool (like USDT)
    function _safeTransfer(address _to, uint256 _amount) internal {
        (bool success, bytes memory data) = address(token).call(
            abi.encodeWithSelector(IERC20.transfer.selector, _to, _amount)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "Token transfer failed");
    }

    /// @dev Handles tokens that don't return bool on transferFrom
    function _safeTransferFrom(address _from, address _to, uint256 _amount) internal {
        (bool success, bytes memory data) = address(token).call(
            abi.encodeWithSelector(IERC20.transferFrom.selector, _from, _to, _amount)
        );
        require(success && (data.length == 0 || abi.decode(data, (bool))), "Token transferFrom failed");
    }
}
