import os
import time
import logging
import random
from typing import Dict, Any, List, Optional
from collections import namedtuple
from dataclasses import dataclass, field

import requests
from web3 import Web3, HTTPProvider
from web3.exceptions import TransactionNotFound, BlockNotFound
from web3.contract import Contract
from dotenv import load_dotenv

# --- Basic Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

load_dotenv() # Load environment variables from .env file

# --- Constants and Type Definitions ---

class TransactionStatus:
    """Enum-like class for tracking the status of a cross-chain transaction."""
    PENDING_CONFIRMATION = 'PENDING_CONFIRMATION' # Detected on source chain, waiting for finality
    READY_TO_RELAY = 'READY_TO_RELAY'         # Confirmed on source chain, ready for submission to destination
    SUBMITTED_TO_DEST = 'SUBMITTED_TO_DEST'     # Relayer has submitted the transaction to the destination chain
    FINALIZED = 'FINALIZED'                   # Successfully processed on the destination chain
    FAILED = 'FAILED'                         # An error occurred at any step

@dataclass
class CrossChainTransaction:
    """Data class to represent a single cross-chain transaction."""
    source_tx_hash: str
    source_chain_id: int
    dest_chain_id: int
    sender: str
    recipient: str
    amount: int
    nonce: int
    status: str = TransactionStatus.PENDING_CONFIRMATION
    dest_tx_hash: Optional[str] = None
    last_updated: float = field(default_factory=time.time)

    def update_status(self, new_status: str):
        self.status = new_status
        self.last_updated = time.time()
        logging.info(f"Transaction {self.source_tx_hash[:10]}... updated to status: {new_status}")

# --- Core Components ---

class BlockchainConnector:
    """Handles the low-level connection to a blockchain node via Web3.py."""

    def __init__(self, chain_id: int, rpc_url: str):
        """
        Initializes the connector for a specific chain.
        Args:
            chain_id (int): The ID of the chain (e.g., 1 for Ethereum Mainnet, 11155111 for Sepolia).
            rpc_url (str): The HTTP RPC endpoint URL for the blockchain node.
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.chain_id = chain_id
        self.rpc_url = rpc_url
        self.web3 = Web3(HTTPProvider(self.rpc_url))

    def is_connected(self) -> bool:
        """Checks if the connection to the node is alive."""
        return self.web3.is_connected()

    def get_latest_block_number(self) -> int:
        """Fetches the most recent block number from the blockchain."""
        try:
            return self.web3.eth.block_number
        except Exception as e:
            self.logger.error(f"[Chain {self.chain_id}] Failed to get latest block number: {e}")
            return 0

    def get_contract(self, address: str, abi: List[Dict[str, Any]]) -> Optional[Contract]:
        """
        Returns a Web3.py Contract instance.
        Args:
            address (str): The contract's address.
            abi (List[Dict[str, Any]]): The contract's ABI.
        Returns:
            Optional[Contract]: A contract object or None if address is invalid.
        """
        if not self.web3.is_address(address):
            self.logger.error(f"[Chain {self.chain_id}] Invalid contract address provided: {address}")
            return None
        return self.web3.eth.contract(address=address, abi=abi)


class TransactionManager:
    """In-memory database to track the state of all ongoing cross-chain transactions."""

    def __init__(self):
        self.transactions: Dict[str, CrossChainTransaction] = {}
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info("Transaction Manager initialized.")

    def register_new_transaction(self, tx: CrossChainTransaction):
        """Adds a new transaction detected on the source chain."""
        if tx.source_tx_hash in self.transactions:
            self.logger.warning(f"Attempted to re-register existing transaction {tx.source_tx_hash[:10]}...")
            return
        self.transactions[tx.source_tx_hash] = tx
        self.logger.info(f"New transaction registered: {tx.source_tx_hash[:10]}... from {tx.sender}")

    def get_transaction(self, source_tx_hash: str) -> Optional[CrossChainTransaction]:
        """Retrieves a transaction by its source chain hash."""
        return self.transactions.get(source_tx_hash)

    def get_transactions_by_status(self, status: str) -> List[CrossChainTransaction]:
        """Finds all transactions with a given status."""
        return [tx for tx in self.transactions.values() if tx.status == status]

    def print_summary(self):
        """Prints a summary of the current state of all transactions."""
        if not self.transactions:
            self.logger.info("Transaction Summary: No transactions being tracked.")
            return

        status_counts = {}
        for status in vars(TransactionStatus).values():
            if isinstance(status, str):
                status_counts[status] = 0
        
        for tx in self.transactions.values():
            status_counts[tx.status] += 1
        
        summary_lines = ["--- Transaction Manager Summary ---"]
        for status, count in status_counts.items():
            summary_lines.append(f"> {status}: {count}")
        summary_lines.append("-----------------------------------")
        self.logger.info('\n'.join(summary_lines))


class EventListener:
    """
    A base class for listening to events on a specific blockchain.
    It polls the chain for new events within a block range and processes them.
    """
    def __init__(
        self, 
        connector: BlockchainConnector, 
        contract: Contract, 
        event_name: str,
        confirmations_required: int = 5
    ):
        self.logger = logging.getLogger(f"{self.__class__.__name__}-{connector.chain_id}")
        self.connector = connector
        self.contract = contract
        self.event_name = event_name
        self.confirmations_required = confirmations_required
        self.last_processed_block = self.connector.get_latest_block_number() - 1 # Start from the block before latest

        self.logger.info(
            f"Initialized for contract {contract.address} on chain {connector.chain_id}. "
            f"Listening for '{event_name}' events. Starting at block {self.last_processed_block}."
        )

    def poll_and_process_events(self):
        """The main loop to fetch and handle new events."""
        try:
            latest_block = self.connector.get_latest_block_number()
            if latest_block <= self.last_processed_block:
                self.logger.debug("No new blocks to process.")
                return

            # To avoid overwhelming the RPC, process in chunks and respect confirmation delay
            from_block = self.last_processed_block + 1
            to_block = latest_block - self.confirmations_required

            if from_block > to_block:
                self.logger.debug(f"Waiting for {self.confirmations_required} confirmations. Latest block is {latest_block}.")
                return
            
            self.logger.debug(f"Scanning for '{self.event_name}' events from block {from_block} to {to_block}.")

            event_filter = self.contract.events[self.event_name].create_filter(
                fromBlock=from_block,
                toBlock=to_block
            )
            events = event_filter.get_all_entries()

            if events:
                self.logger.info(f"Found {len(events)} new '{self.event_name}' event(s).")
                for event in events:
                    self._process_event(event)
            
            self.last_processed_block = to_block

        except Exception as e:
            self.logger.error(f"An error occurred during event polling: {e}")
            # In a real scenario, you might want to add backoff logic here
            time.sleep(10)
    
    def _process_event(self, event: Dict[str, Any]):
        """Placeholder for event processing logic. To be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement the _process_event method.")


class SourceChainListener(EventListener):
    """Listens for `TokensLocked` events on the source chain."""
    def __init__(self, *args, tx_manager: TransactionManager, dest_chain_id: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.tx_manager = tx_manager
        self.dest_chain_id = dest_chain_id
    
    def _process_event(self, event: Dict[str, Any]):
        """Processes a TokensLocked event, creating a new CrossChainTransaction."""
        args = event['args']
        tx_hash = event['transactionHash'].hex()
        self.logger.info(f"Processing TokensLocked event from transaction {tx_hash[:10]}...")
        
        new_tx = CrossChainTransaction(
            source_tx_hash=tx_hash,
            source_chain_id=self.connector.chain_id,
            dest_chain_id=self.dest_chain_id,
            sender=args['sender'],
            recipient=args['recipient'],
            amount=args['amount'],
            nonce=args['nonce'],
            status=TransactionStatus.READY_TO_RELAY # After confirmations, it's ready
        )
        self.tx_manager.register_new_transaction(new_tx)


class DestinationChainListener(EventListener):
    """Listens for `TokensMinted` events on the destination chain."""
    def __init__(self, *args, tx_manager: TransactionManager, **kwargs):
        super().__init__(*args, **kwargs)
        self.tx_manager = tx_manager

    def _process_event(self, event: Dict[str, Any]):
        """Processes a TokensMinted event, finalizing the corresponding transaction."""
        args = event['args']
        source_tx_hash = args['sourceTxHash'].hex()
        dest_tx_hash = event['transactionHash'].hex()
        self.logger.info(f"Processing TokensMinted event from tx {dest_tx_hash[:10]}... corresponding to source tx {source_tx_hash[:10]}...")

        transaction = self.tx_manager.get_transaction(source_tx_hash)
        if transaction:
            transaction.update_status(TransactionStatus.FINALIZED)
            transaction.dest_tx_hash = dest_tx_hash
        else:
            # This can happen if the listener started after the source event was processed
            # In a real system, you'd need a way to reconcile this, e.g., by fetching from a database.
            self.logger.warning(f"Received mint event for an untracked source transaction hash: {source_tx_hash}")


class RelayerSimulator:
    """
    Simulates the role of a relayer, which picks up confirmed transactions
    and submits them to the destination chain.
    """
    def __init__(self, tx_manager: TransactionManager, dest_connector: BlockchainConnector):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.tx_manager = tx_manager
        self.dest_connector = dest_connector # In a real system, it would have a wallet/signer
        self.external_gas_api = "https://api.etherscan.io/api?module=gastracker&action=gasoracle"
        self.logger.info("Relayer Simulator initialized.")

    def _get_simulated_gas_price(self) -> int:
        """
        Simulates fetching gas price from an external API.
        Uses a fallback if the API fails.
        """
        try:
            response = requests.get(self.external_gas_api)
            response.raise_for_status() # Raise an exception for bad status codes
            gas_price_gwei = int(response.json()['result']['ProposeGasPrice'])
            self.logger.info(f"Fetched gas price from external API: {gas_price_gwei} Gwei")
            return self.dest_connector.web3.to_wei(gas_price_gwei, 'gwei')
        except Exception as e:
            self.logger.warning(f"Could not fetch gas price from API ({e}), using random fallback.")
            return self.dest_connector.web3.to_wei(random.randint(20, 50), 'gwei')

    def process_pending_transactions(self):
        """Scans for transactions ready to be relayed and processes them."""
        ready_to_relay = self.tx_manager.get_transactions_by_status(TransactionStatus.READY_TO_RELAY)
        if not ready_to_relay:
            return

        self.logger.info(f"Found {len(ready_to_relay)} transaction(s) ready to be relayed.")
        for tx in ready_to_relay:
            self.logger.info(f"Relaying transaction {tx.source_tx_hash[:10]}... to chain {tx.dest_chain_id}")
            gas_price = self._get_simulated_gas_price()
            
            # --- SIMULATION --- #
            # In a real system, this is where you would build, sign, and send the transaction.
            # For example: contract.functions.mint(...).transact({'from': ..., 'gasPrice': ...})
            # Here, we just simulate the process.
            self.logger.info(f"Simulating submission for tx {tx.source_tx_hash[:10]}... with gas price {self.dest_connector.web3.from_wei(gas_price, 'gwei')} Gwei")
            time.sleep(random.uniform(1, 3)) # Simulate network latency
            
            # Fake a destination transaction hash
            simulated_dest_hash = '0x' + os.urandom(32).hex()
            tx.dest_tx_hash = simulated_dest_hash
            tx.update_status(TransactionStatus.SUBMITTED_TO_DEST)
            self.logger.info(f"Transaction {tx.source_tx_hash[:10]}... successfully submitted to destination. Simulated dest hash: {simulated_dest_hash[:10]}...")

# --- Main Simulation Setup ---

def main():
    """Main function to run the simulation."""
    logger = logging.getLogger("main")
    logger.info("--- Cross-Chain Bridge Listener Simulation Starting ---")

    # --- Configuration from .env --- #
    SOURCE_CHAIN_ID = int(os.getenv("SOURCE_CHAIN_ID", 11155111)) # Sepolia default
    DEST_CHAIN_ID = int(os.getenv("DEST_CHAIN_ID", 80001))     # Mumbai default
    SOURCE_RPC_URL = os.getenv("SOURCE_RPC_URL")
    DEST_RPC_URL = os.getenv("DEST_RPC_URL")
    SOURCE_CONTRACT_ADDRESS = os.getenv("SOURCE_CONTRACT_ADDRESS")
    DEST_CONTRACT_ADDRESS = os.getenv("DEST_CONTRACT_ADDRESS")

    if not all([SOURCE_RPC_URL, DEST_RPC_URL, SOURCE_CONTRACT_ADDRESS, DEST_CONTRACT_ADDRESS]):
        logger.error("Missing required environment variables. Please check your .env file.")
        return

    # In a real application, ABI would be loaded from a JSON file
    SOURCE_CONTRACT_ABI = [{"type":"event","name":"TokensLocked","inputs":[{"name":"sender","type":"address","indexed":True},{"name":"recipient","type":"address","indexed":True},{"name":"amount","type":"uint256","indexed":False},{"name":"nonce","type":"uint256","indexed":False}],"anonymous":False}]
    DEST_CONTRACT_ABI = [{"type":"event","name":"TokensMinted","inputs":[{"name":"recipient","type":"address","indexed":True},{"name":"amount","type":"uint256","indexed":False},{"name":"sourceTxHash","type":"bytes32","indexed":True}],"anonymous":False}]

    # --- Initialize Components --- #
    try:
        source_connector = BlockchainConnector(SOURCE_CHAIN_ID, SOURCE_RPC_URL)
        dest_connector = BlockchainConnector(DEST_CHAIN_ID, DEST_RPC_URL)

        if not source_connector.is_connected() or not dest_connector.is_connected():
            logger.error("Failed to connect to one or more blockchain nodes. Exiting.")
            return
        logger.info(f"Successfully connected to Source Chain (ID: {SOURCE_CHAIN_ID}) and Destination Chain (ID: {DEST_CHAIN_ID})")
        
        source_contract = source_connector.get_contract(SOURCE_CONTRACT_ADDRESS, SOURCE_CONTRACT_ABI)
        dest_contract = dest_connector.get_contract(DEST_CONTRACT_ADDRESS, DEST_CONTRACT_ABI)

        if not source_contract or not dest_contract:
             logger.error("Failed to initialize one or more contracts. Check addresses.")
             return

        tx_manager = TransactionManager()
        relayer = RelayerSimulator(tx_manager, dest_connector)

        source_listener = SourceChainListener(
            connector=source_connector, 
            contract=source_contract, 
            event_name='TokensLocked',
            tx_manager=tx_manager,
            dest_chain_id=DEST_CHAIN_ID,
            confirmations_required=3
        )

        dest_listener = DestinationChainListener(
            connector=dest_connector,
            contract=dest_contract,
            event_name='TokensMinted',
            tx_manager=tx_manager,
            confirmations_required=3
        )

    except Exception as e:
        logger.error(f"Error during initialization: {e}")
        return

    # --- Main Simulation Loop --- #
    POLL_INTERVAL_SECONDS = 15
    SUMMARY_INTERVAL_SECONDS = 60
    last_summary_time = 0

    logger.info(f"Starting main loop. Polling every {POLL_INTERVAL_SECONDS} seconds.")
    try:
        while True:
            source_listener.poll_and_process_events()
            dest_listener.poll_and_process_events()
            
            relayer.process_pending_transactions()

            if time.time() - last_summary_time > SUMMARY_INTERVAL_SECONDS:
                tx_manager.print_summary()
                last_summary_time = time.time()
            
            time.sleep(POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("--- Simulation stopped by user ---")
        tx_manager.print_summary()

if __name__ == "__main__":
    # To run this simulation, you need a .env file with the following:
    # SOURCE_RPC_URL=https://sepolia.infura.io/v3/YOUR_INFURA_ID
    # DEST_RPC_URL=https://rpc-mumbai.maticvigil.com/
    # SOURCE_CONTRACT_ADDRESS=0x.... (A contract that emits TokensLocked)
    # DEST_CONTRACT_ADDRESS=0x.... (A contract that emits TokensMinted)
    # You can use any public contract addresses that have these events for testing.
    # Example Sepolia contract with similar events: 0xBe5a34244B484215B2F929d2a67768565a5113B3
    # Example Mumbai contract with similar events: 0x36FE9e969a53130d20F46654f5A10A9A483fD15d
    main()
