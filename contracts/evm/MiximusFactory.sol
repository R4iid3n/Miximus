// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.19;

import "./MiximusNative.sol";
import "./MiximusERC20.sol";

/**
 * @title MiximusFactory
 * @notice Factory contract to deploy and manage mixer pools for any asset on any EVM chain.
 *         Deploys MiximusNative pools for native currency and MiximusERC20 pools for tokens.
 *
 * Deploy this factory once per chain, then create pools for each asset/denomination pair.
 */
contract MiximusFactory {
    // =========================================================================
    //                            STATE VARIABLES
    // =========================================================================

    address public owner;

    /// @notice Registry of all deployed mixer pools: keccak256(token, denomination) => pool address
    mapping(bytes32 => address) public pools;

    /// @notice List of all deployed pool addresses
    address[] public allPools;

    /// @notice Native currency sentinel address
    address public constant NATIVE = address(0);

    // =========================================================================
    //                               EVENTS
    // =========================================================================

    event NativePoolCreated(
        address indexed pool,
        uint256 denomination,
        string symbol,
        uint256 chainId
    );

    event ERC20PoolCreated(
        address indexed pool,
        address indexed token,
        uint256 denomination,
        string symbol,
        uint256 chainId
    );

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    // =========================================================================
    //                            CONSTRUCTOR
    // =========================================================================

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    // =========================================================================
    //                         POOL CREATION
    // =========================================================================

    /**
     * @notice Create a mixer pool for the chain's native currency
     * @param _denomination Amount in wei (e.g., 1 ether = 1e18)
     * @param _symbol Symbol for this pool (e.g., "ETH", "BNB", "AVAX")
     * @param _vk The zkSNARK verifying key
     * @param _vkGammaABC The verifying key gamma ABC component
     * @return pool The deployed pool address
     */
    function createNativePool(
        uint256 _denomination,
        string memory _symbol,
        uint256[14] memory _vk,
        uint256[] memory _vkGammaABC
    ) external onlyOwner returns (address pool) {
        bytes32 key = keccak256(abi.encodePacked(NATIVE, _denomination));
        require(pools[key] == address(0), "Pool already exists");

        MiximusNative nativePool = new MiximusNative(
            _denomination,
            _symbol,
            _vk,
            _vkGammaABC
        );

        pool = address(nativePool);
        pools[key] = pool;
        allPools.push(pool);

        emit NativePoolCreated(pool, _denomination, _symbol, block.chainid);
    }

    /**
     * @notice Create a mixer pool for an ERC20/BEP20 token
     * @param _token The token contract address
     * @param _denomination Amount in token's smallest unit
     * @param _symbol Symbol for this pool (e.g., "USDT", "USDC", "WBTC")
     * @param _vk The zkSNARK verifying key
     * @param _vkGammaABC The verifying key gamma ABC component
     * @return pool The deployed pool address
     */
    function createERC20Pool(
        address _token,
        uint256 _denomination,
        string memory _symbol,
        uint256[14] memory _vk,
        uint256[] memory _vkGammaABC
    ) external onlyOwner returns (address pool) {
        bytes32 key = keccak256(abi.encodePacked(_token, _denomination));
        require(pools[key] == address(0), "Pool already exists");

        MiximusERC20 erc20Pool = new MiximusERC20(
            _token,
            _denomination,
            _symbol,
            _vk,
            _vkGammaABC
        );

        pool = address(erc20Pool);
        pools[key] = pool;
        allPools.push(pool);

        emit ERC20PoolCreated(pool, _token, _denomination, _symbol, block.chainid);
    }

    // =========================================================================
    //                         VIEW FUNCTIONS
    // =========================================================================

    function getPool(address _token, uint256 _denomination) external view returns (address) {
        bytes32 key = keccak256(abi.encodePacked(_token, _denomination));
        return pools[key];
    }

    function getNativePool(uint256 _denomination) external view returns (address) {
        bytes32 key = keccak256(abi.encodePacked(NATIVE, _denomination));
        return pools[key];
    }

    function totalPools() external view returns (uint256) {
        return allPools.length;
    }

    // =========================================================================
    //                          ADMIN
    // =========================================================================

    function transferOwnership(address _newOwner) external onlyOwner {
        require(_newOwner != address(0), "Invalid address");
        emit OwnershipTransferred(owner, _newOwner);
        owner = _newOwner;
    }
}
