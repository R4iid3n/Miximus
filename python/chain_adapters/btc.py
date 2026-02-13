"""
Bitcoin Custodial Adapter

Implements a custodial mixing model for Bitcoin. Unlike EVM chains where
smart contracts enforce the mixer logic on-chain, Bitcoin has no general-
purpose smart contract capability. Instead, the service operates a custodial
UTXO pool:

  1. Users send BTC to the service wallet address (deposit).
  2. The service monitors incoming payments via Blockstream's Esplora API.
  3. Once confirmed, the user's funds enter the common UTXO pool.
  4. On withdrawal, the service sends BTC from its pool to the recipient.

Privacy comes from the aggregation of many users' deposits into a single
UTXO pool -- the on-chain link between depositor and recipient is broken
because the withdrawal transaction spends arbitrary UTXOs from the pool,
not the specific UTXO the depositor created.

No zkSNARK proofs are needed on Bitcoin. The service itself IS the mixer.

Dependencies:
    pip install bit requests

API Reference:
    Blockstream Esplora: https://github.com/Blockstream/esplora/blob/master/API.md
"""

import logging
import time
from typing import Dict, List, Optional, Any

import requests
from bit import Key, PrivateKeyTestnet
from bit.exceptions import InsufficientFunds
from bit.network import NetworkAPI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ESPLORA_MAINNET = "https://blockstream.info/api"
ESPLORA_TESTNET = "https://blockstream.info/testnet/api"

# Fixed denomination for the mixer -- every deposit/withdrawal is exactly
# this amount (in satoshis). Using a fixed denomination is critical for
# privacy: variable amounts would make tracing trivial.
DENOMINATION_SATOSHIS = 200_000  # 0.002 BTC

# Minimum confirmations before a deposit is considered final.
MIN_CONFIRMATIONS = 3

# HTTP request timeout in seconds for Esplora API calls.
REQUEST_TIMEOUT = 30

# Maximum number of retry attempts for transient API errors.
MAX_RETRIES = 3

# Delay between retries in seconds (doubles on each retry).
RETRY_BASE_DELAY = 2


class BitcoinAdapterError(Exception):
    """Base exception for all Bitcoin adapter errors."""
    pass


class PaymentNotFoundError(BitcoinAdapterError):
    """Raised when a transaction cannot be found on the blockchain."""
    pass


class InsufficientConfirmationsError(BitcoinAdapterError):
    """Raised when a transaction does not have enough confirmations."""
    pass


class BroadcastError(BitcoinAdapterError):
    """Raised when transaction broadcast fails."""
    pass


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class BitcoinAdapter:
    """
    Bitcoin custodial mixer adapter.

    This adapter manages a service wallet whose UTXOs form the mixer pool.
    It talks to the Bitcoin network exclusively through Blockstream's Esplora
    REST API -- no local Bitcoin node is required.

    Args:
        private_key_hex: Service wallet private key in WIF or hex format.
        network: Either ``'testnet'`` or ``'mainnet'``.
        denomination: Fixed deposit/withdrawal amount in satoshis.
            Defaults to ``DENOMINATION_SATOSHIS`` (200 000 sat).
        min_confirmations: Minimum block confirmations for deposit
            finality. Defaults to ``MIN_CONFIRMATIONS`` (3).
    """

    def __init__(
        self,
        private_key_hex: str,
        network: str = "testnet",
        denomination: int = DENOMINATION_SATOSHIS,
        min_confirmations: int = MIN_CONFIRMATIONS,
    ):
        if network not in ("testnet", "mainnet"):
            raise ValueError(f"Unsupported network: {network!r}. Use 'testnet' or 'mainnet'.")

        self.network = network
        self.denomination = denomination
        self.min_confirmations = min_confirmations

        # Esplora base URL -- no trailing slash.
        self.api_base = ESPLORA_TESTNET if network == "testnet" else ESPLORA_MAINNET

        # Initialize the key object from the ``bit`` library.
        # Strip 0x prefix if present, then convert hex to bytes.
        clean_hex = private_key_hex.replace("0x", "")
        try:
            pk_bytes = bytes.fromhex(clean_hex)
            if network == "testnet":
                self.key = PrivateKeyTestnet.from_bytes(pk_bytes)
            else:
                self.key = Key.from_bytes(pk_bytes)
        except Exception as exc:
            raise BitcoinAdapterError(
                f"Failed to initialize private key: {exc}"
            ) from exc

        # Persistent HTTP session for connection pooling.
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "User-Agent": "MiximusBTCAdapter/1.0",
        })

        logger.info(
            "BitcoinAdapter initialized: network=%s address=%s denomination=%d sat",
            self.network,
            self.get_address(),
            self.denomination,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_address(self) -> str:
        """
        Return the service wallet's Bitcoin address.

        On testnet this will be a ``tb1...`` (bech32) or ``m/n...`` address
        depending on the ``bit`` library version. On mainnet it will be a
        ``bc1...`` or ``1...`` address.
        """
        return self.key.address

    def verify_payment(
        self,
        tx_hash: str,
        expected_amount: int,
    ) -> Dict[str, Any]:
        """
        Verify an incoming payment to the service wallet.

        Looks up the transaction on the blockchain via Esplora, checks that
        at least one output pays the service address, and that the total
        paid amount meets ``expected_amount``.

        Args:
            tx_hash: The transaction ID to look up.
            expected_amount: Expected payment amount in satoshis.

        Returns:
            A dict with keys:
                ``verified`` (bool) -- whether the payment is confirmed
                    and meets the expected amount.
                ``confirmations`` (int) -- current confirmation count.
                ``amount`` (int) -- total satoshis received by the
                    service address in this transaction.
                ``sender`` (str | None) -- address of the first input
                    (best-effort; may be ``None`` for coinbase or
                    unusual script types).

        Raises:
            PaymentNotFoundError: If the transaction does not exist.
            BitcoinAdapterError: On API communication errors.
        """
        tx_data = self._get_transaction(tx_hash)

        # Determine confirmations.
        confirmations = self._extract_confirmations(tx_data)

        # Sum outputs paying the service address.
        service_address = self.get_address()
        amount_received = 0
        for vout in tx_data.get("vout", []):
            scriptpubkey_address = vout.get("scriptpubkey_address", "")
            if scriptpubkey_address == service_address:
                amount_received += vout.get("value", 0)

        # Best-effort sender extraction: take the address of the first
        # input's previous output.
        sender = None
        vin_list = tx_data.get("vin", [])
        if vin_list:
            prevout = vin_list[0].get("prevout", {})
            sender = prevout.get("scriptpubkey_address")

        verified = (
            confirmations >= self.min_confirmations
            and amount_received >= expected_amount
        )

        result = {
            "verified": verified,
            "confirmations": confirmations,
            "amount": amount_received,
            "sender": sender,
        }

        logger.info(
            "verify_payment tx=%s confirmed=%d amount=%d expected=%d verified=%s",
            tx_hash,
            confirmations,
            amount_received,
            expected_amount,
            verified,
        )

        return result

    def get_balance(self) -> int:
        """
        Get the service wallet's confirmed balance in satoshis.

        This queries Esplora's address endpoint and returns the
        confirmed balance (chain stats funded minus spent).

        Returns:
            Confirmed balance in satoshis.
        """
        address = self.get_address()
        data = self._api_get(f"/address/{address}")

        chain_stats = data.get("chain_stats", {})
        funded = chain_stats.get("funded_txo_sum", 0)
        spent = chain_stats.get("spent_txo_sum", 0)
        balance = funded - spent

        logger.debug("get_balance address=%s balance=%d sat", address, balance)
        return balance

    def send_btc(
        self,
        recipient_address: str,
        amount_satoshis: int,
    ) -> str:
        """
        Create, sign, and broadcast a withdrawal transaction.

        The transaction pays ``amount_satoshis`` to ``recipient_address``
        from the service wallet's UTXO pool.  Change (if any) returns to
        the service wallet.

        Args:
            recipient_address: Destination Bitcoin address.
            amount_satoshis: Amount to send in satoshis.

        Returns:
            The broadcast transaction ID (hex string).

        Raises:
            ValueError: If the amount is non-positive.
            InsufficientFunds: If the wallet lacks sufficient UTXOs.
            BroadcastError: If Esplora rejects the transaction.
            BitcoinAdapterError: On other failures.
        """
        if amount_satoshis <= 0:
            raise ValueError(f"Amount must be positive, got {amount_satoshis}")

        logger.info(
            "send_btc to=%s amount=%d sat",
            recipient_address,
            amount_satoshis,
        )

        # Refresh UTXOs from the network so the ``bit`` library has an
        # up-to-date view of spendable outputs.
        self._refresh_utxos()

        # Build and sign the transaction using ``bit``.  The library
        # handles UTXO selection and change output automatically.
        try:
            tx_hex = self.key.create_transaction(
                [(recipient_address, amount_satoshis, "satoshi")],
            )
        except InsufficientFunds:
            balance = self.get_balance()
            raise InsufficientFunds(
                f"Insufficient funds: wallet has {balance} sat, "
                f"need {amount_satoshis} sat plus fees"
            )
        except Exception as exc:
            raise BitcoinAdapterError(
                f"Failed to create transaction: {exc}"
            ) from exc

        # Broadcast via Esplora.
        tx_hash = self._broadcast_transaction(tx_hex)

        logger.info("send_btc broadcast OK txid=%s", tx_hash)
        return tx_hash

    def get_utxos(self) -> List[Dict[str, Any]]:
        """
        List unspent transaction outputs (UTXOs) for the service wallet.

        Returns:
            A list of dicts, each containing:
                ``txid`` (str) -- transaction ID.
                ``vout`` (int) -- output index.
                ``value`` (int) -- amount in satoshis.
                ``status`` (dict) -- confirmation status from Esplora.
        """
        address = self.get_address()
        utxos = self._api_get(f"/address/{address}/utxo")

        if not isinstance(utxos, list):
            logger.warning("Unexpected UTXO response type: %s", type(utxos))
            return []

        logger.debug(
            "get_utxos address=%s count=%d",
            address,
            len(utxos),
        )
        return utxos

    def get_tx_confirmations(self, tx_hash: str) -> int:
        """
        Get the number of confirmations for a transaction.

        Args:
            tx_hash: The transaction ID.

        Returns:
            Number of confirmations (0 if unconfirmed).

        Raises:
            PaymentNotFoundError: If the transaction does not exist.
        """
        tx_data = self._get_transaction(tx_hash)
        return self._extract_confirmations(tx_data)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def wait_for_confirmation(
        self,
        tx_hash: str,
        target_confirmations: Optional[int] = None,
        poll_interval: int = 30,
        timeout: int = 3600,
    ) -> int:
        """
        Block until a transaction reaches the desired confirmation depth.

        This is a convenience method for callers that want to synchronously
        wait for finality.

        Args:
            tx_hash: The transaction ID to watch.
            target_confirmations: Required confirmations. Defaults to
                ``self.min_confirmations``.
            poll_interval: Seconds between polls.
            timeout: Maximum wait time in seconds before giving up.

        Returns:
            Final confirmation count.

        Raises:
            TimeoutError: If the timeout expires before the target is met.
            PaymentNotFoundError: If the transaction cannot be found.
        """
        if target_confirmations is None:
            target_confirmations = self.min_confirmations

        deadline = time.monotonic() + timeout
        while True:
            confirmations = self.get_tx_confirmations(tx_hash)
            if confirmations >= target_confirmations:
                logger.info(
                    "wait_for_confirmation tx=%s reached %d confirmations",
                    tx_hash,
                    confirmations,
                )
                return confirmations

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Transaction {tx_hash} has only {confirmations} "
                    f"confirmations after {timeout}s (need {target_confirmations})"
                )

            logger.debug(
                "wait_for_confirmation tx=%s at %d/%d, sleeping %ds",
                tx_hash,
                confirmations,
                target_confirmations,
                poll_interval,
            )
            time.sleep(poll_interval)

    def estimate_fee(self) -> Dict[str, int]:
        """
        Fetch the recommended fee rates from Esplora.

        Returns:
            A dict mapping confirmation-target strings (e.g. ``"1"``,
            ``"3"``, ``"6"``) to fee rates in sat/vbyte.
        """
        return self._api_get("/fee-estimates")

    # ------------------------------------------------------------------
    # Internal / private methods
    # ------------------------------------------------------------------

    def _api_get(self, path: str) -> Any:
        """
        Perform a GET request to the Esplora API with retries.

        Args:
            path: URL path relative to ``self.api_base`` (must start with ``/``).

        Returns:
            Parsed JSON response.

        Raises:
            BitcoinAdapterError: After all retries are exhausted or on
                non-retryable errors.
        """
        url = f"{self.api_base}{path}"
        last_exc = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.get(url, timeout=REQUEST_TIMEOUT)

                if response.status_code == 404:
                    raise PaymentNotFoundError(
                        f"Resource not found: {path}"
                    )

                if response.status_code == 429:
                    # Rate-limited -- back off and retry.
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Esplora rate limit hit on %s, retrying in %ds (attempt %d/%d)",
                        path, delay, attempt, MAX_RETRIES,
                    )
                    time.sleep(delay)
                    continue

                response.raise_for_status()
                return response.json()

            except PaymentNotFoundError:
                raise
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Esplora request failed (%s), retrying in %ds (attempt %d/%d): %s",
                        path, delay, attempt, MAX_RETRIES, exc,
                    )
                    time.sleep(delay)
                else:
                    break

        raise BitcoinAdapterError(
            f"Esplora API request failed after {MAX_RETRIES} attempts: {last_exc}"
        )

    def _api_post(self, path: str, data: str) -> str:
        """
        Perform a POST request to the Esplora API with retries.

        Args:
            path: URL path relative to ``self.api_base``.
            data: Raw request body (e.g. hex-encoded transaction).

        Returns:
            Response body text.

        Raises:
            BitcoinAdapterError: After all retries are exhausted.
        """
        url = f"{self.api_base}{path}"
        last_exc = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.post(
                    url,
                    data=data,
                    headers={"Content-Type": "text/plain"},
                    timeout=REQUEST_TIMEOUT,
                )

                if response.status_code == 429:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Esplora rate limit on POST %s, retrying in %ds",
                        path, delay,
                    )
                    time.sleep(delay)
                    continue

                if not response.ok:
                    error_body = response.text[:500]
                    raise BroadcastError(
                        f"Esplora POST {path} returned {response.status_code}: {error_body}"
                    )

                return response.text.strip()

            except BroadcastError:
                raise
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Esplora POST failed (%s), retrying in %ds: %s",
                        path, delay, exc,
                    )
                    time.sleep(delay)
                else:
                    break

        raise BitcoinAdapterError(
            f"Esplora API POST failed after {MAX_RETRIES} attempts: {last_exc}"
        )

    def _get_transaction(self, tx_hash: str) -> Dict[str, Any]:
        """
        Fetch full transaction data from Esplora.

        Args:
            tx_hash: Transaction ID (64-char hex string).

        Returns:
            Parsed transaction JSON.

        Raises:
            PaymentNotFoundError: If the transaction does not exist.
        """
        tx_hash = tx_hash.strip().lower()
        if len(tx_hash) != 64:
            raise ValueError(
                f"Invalid transaction hash length: expected 64 hex chars, got {len(tx_hash)}"
            )

        return self._api_get(f"/tx/{tx_hash}")

    def _extract_confirmations(self, tx_data: Dict[str, Any]) -> int:
        """
        Compute the number of confirmations from Esplora transaction data.

        Esplora includes a ``status.block_height`` field for confirmed
        transactions. We compare that against the current chain tip to
        derive the confirmation count.

        Args:
            tx_data: Transaction dict from Esplora.

        Returns:
            Number of confirmations (0 if unconfirmed).
        """
        status = tx_data.get("status", {})
        if not status.get("confirmed", False):
            return 0

        block_height = status.get("block_height")
        if block_height is None:
            return 0

        tip_height = self._get_tip_height()
        confirmations = tip_height - block_height + 1
        return max(confirmations, 0)

    def _get_tip_height(self) -> int:
        """
        Get the current blockchain tip height.

        Returns:
            Block height of the most recent block.
        """
        url = f"{self.api_base}/blocks/tip/height"
        last_exc = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                return int(response.text.strip())
            except (requests.exceptions.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    time.sleep(delay)

        raise BitcoinAdapterError(
            f"Failed to get chain tip height after {MAX_RETRIES} attempts: {last_exc}"
        )

    def _refresh_utxos(self) -> None:
        """
        Refresh the ``bit`` key's UTXO set from the network.

        The ``bit`` library maintains its own internal UTXO cache. We
        force a refresh so that ``create_transaction`` operates on
        current data. We use the Esplora UTXO list and inject it into
        the key object rather than relying on ``bit``'s default
        backend, which may use different API providers.
        """
        utxos = self.get_utxos()
        if not utxos:
            logger.debug("No UTXOs found for address %s", self.get_address())
            return

        # Convert Esplora UTXO format to bit's internal Unspent format.
        from bit.network.meta import Unspent

        unspent_list = []
        for u in utxos:
            # Only include confirmed UTXOs for reliability.
            status = u.get("status", {})
            confirmed = status.get("confirmed", False)
            if not confirmed:
                continue

            unspent_list.append(
                Unspent(
                    amount=u["value"],
                    confirmations=1,  # Confirmed; exact count not needed by bit.
                    script=None,      # bit will fetch if needed.
                    txid=u["txid"],
                    txindex=u["vout"],
                )
            )

        if unspent_list:
            self.key.unspents = unspent_list

        logger.debug(
            "Refreshed UTXOs: %d confirmed out of %d total",
            len(unspent_list),
            len(utxos),
        )

    def _broadcast_transaction(self, tx_hex: str) -> str:
        """
        Broadcast a signed transaction via Esplora's ``POST /tx`` endpoint.

        Args:
            tx_hex: Hex-encoded signed transaction.

        Returns:
            Transaction ID returned by the API.

        Raises:
            BroadcastError: If the API rejects the transaction.
        """
        txid = self._api_post("/tx", tx_hex)

        # Esplora returns the txid as plain text on success.
        if len(txid) != 64:
            raise BroadcastError(
                f"Unexpected broadcast response (expected 64-char txid): {txid[:200]}"
            )

        return txid

    # ------------------------------------------------------------------
    # Dunder methods
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<BitcoinAdapter network={self.network} "
            f"address={self.get_address()} "
            f"denomination={self.denomination} sat>"
        )
