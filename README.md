# Common Repository: Cross-Chain Bridge Event Listener Simulation

This repository contains a Python-based simulation of a critical component for a cross-chain bridge: the event listener and relayer service. This script is designed to monitor events on a source blockchain, process them, and simulate the action of relaying the transaction to a destination blockchain.

## Concept

A cross-chain bridge allows users to transfer assets or data from one blockchain (e.g., Ethereum) to another (e.g., Polygon). A common mechanism for this is "lock-and-mint":

1.  **Lock**: A user locks their assets (e.g., USDC) in a smart contract on the source chain. This action emits an event, such as `TokensLocked`.
2.  **Verify**: A network of off-chain listeners (or oracles) detects and verifies this event after a certain number of block confirmations to prevent reorgs.
3.  **Relay**: A relayer service takes the verified event data, signs it, and submits it as a transaction to a corresponding smart contract on the destination chain.
4.  **Mint**: The destination chain contract verifies the relayer's signature and mints a corresponding wrapped asset (e.g., USDC.polygon) to the user's address.

This script simulates the off-chain components responsible for steps 2 and 3, which are crucial for the bridge's liveness and security.

## Code Architecture

The script is designed with a modular, class-based architecture to separate concerns and simulate a real-world microservice.

-   **`BlockchainConnector`**: A low-level wrapper around the `web3.py` library. It manages the connection to a specific blockchain's RPC endpoint and provides methods for fetching blocks and interacting with contracts.

-   **`TransactionManager`**: An in-memory state machine. It acts as a database for tracking the lifecycle of each cross-chain transaction, from its detection on the source chain (`PENDING_CONFIRMATION`) to its completion on the destination chain (`FINALIZED`) or failure (`FAILED`).

-   **`EventListener` (Base Class)**: A generic event poller. It periodically queries a blockchain for new events from a specific contract, managing block ranges and confirmation delays. It is subclassed for specific chains.

-   **`SourceChainListener`**: A subclass of `EventListener` that specifically listens for `TokensLocked` events. When a confirmed event is found, it creates a `CrossChainTransaction` object and registers it with the `TransactionManager`.

-   **`DestinationChainListener`**: A subclass of `EventListener` that listens for `TokensMinted` events on the destination chain. Upon detection, it finds the corresponding transaction in the `TransactionManager` and updates its status to `FINALIZED`.

-   **`RelayerSimulator`**: This component simulates the relayer's job. It queries the `TransactionManager` for transactions that are confirmed and ready to be relayed. It then simulates fetching gas prices (using the `requests` library to call an external API), crafting a transaction, and submitting it to the destination chain.

## How it Works

The simulation runs in a continuous loop, orchestrating the different components:

1.  **Initialization**: The `main` function reads configuration from a `.env` file (RPC URLs, contract addresses), initializes connectors for both the source and destination chains, and sets up all the manager, listener, and relayer objects.

2.  **Polling Source Chain**: The `SourceChainListener` continuously polls the source chain for `TokensLocked` events. It waits for a configured number of block confirmations before processing an event to ensure finality.

3.  **Registering Transaction**: Once a `TokensLocked` event is confirmed, it is processed. A `CrossChainTransaction` data object is created and stored in the `TransactionManager` with the status `READY_TO_RELAY`.

4.  **Relaying**: The `RelayerSimulator` periodically checks the `TransactionManager` for transactions in the `READY_TO_RELAY` state. For each one, it simulates the submission process:
    -   Fetches a gas price from an external API (e.g., Etherscan's Gas Oracle).
    -   Waits for a random delay to simulate network latency.
    -   Updates the transaction's status to `SUBMITTED_TO_DEST`.

5.  **Polling Destination Chain**: Concurrently, the `DestinationChainListener` polls the destination chain for `TokensMinted` events. This event contains the original source transaction hash, linking the two actions.

6.  **Finalizing Transaction**: When a `TokensMinted` event is found, the listener looks up the transaction in the `TransactionManager` using the source transaction hash and updates its status to `FINALIZED`.

7.  **Logging & Summary**: The script provides continuous logging of its activities. Periodically, it prints a summary from the `TransactionManager` showing how many transactions are in each stage of the process.

## Usage Example

1.  **Clone the repository and create a virtual environment:**

    ```bash
    git clone <repo_url>
    cd common
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

2.  **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

3.  **Create a `.env` file** in the root directory. You will need RPC URLs for two EVM-compatible testnets (e.g., Sepolia and Mumbai) and addresses of contracts that emit the expected events. You can often find public contracts on block explorers for this purpose.

    **`.env` file example:**

    ```ini
    # Get a free RPC URL from a provider like Infura, Alchemy, etc.
    SOURCE_RPC_URL="https://sepolia.infura.io/v3/YOUR_INFURA_PROJECT_ID"
    DEST_RPC_URL="https://polygon-mumbai.g.alchemy.com/v2/YOUR_ALCHEMY_PROJECT_ID"

    # Publicly deployed contracts on testnets for demonstration purposes
    # These contracts emit events with a similar structure.
    SOURCE_CONTRACT_ADDRESS="0xBe5a34244B484215B2F929d2a67768565a5113B3"
    DEST_CONTRACT_ADDRESS="0x36FE9e969a53130d20F46654f5A10A9A483fD15d"
    ```

4.  **Run the script:**

    ```bash
    python bridge_listener.py
    ```

5.  **Observe the output:** The console will display logs as the script connects to the chains, polls for events, and simulates the relaying process.

    **Example Log Output:**

    ```
    2023-10-27 15:30:00 - INFO - [main] - --- Cross-Chain Bridge Listener Simulation Starting ---
    2023-10-27 15:30:02 - INFO - [main] - Successfully connected to Source Chain (ID: 11155111) and Destination Chain (ID: 80001)
    2023-10-27 15:30:02 - INFO - [SourceChainListener-11155111] - Initialized for contract 0xBe5a... on chain 11155111. Listening for 'TokensLocked' events...
    2023-10-27 15:30:02 - INFO - [main] - Starting main loop. Polling every 15 seconds.
    ...
    2023-10-27 15:30:45 - INFO - [SourceChainListener-11155111] - Found 1 new 'TokensLocked' event(s).
    2023-10-27 15:30:45 - INFO - [SourceChainListener-11155111] - Processing TokensLocked event from transaction 0x1a2b3c...
    2023-10-27 15:30:45 - INFO - [TransactionManager] - New transaction registered: 0x1a2b3c... from 0xSenderAddress...
    ...
    2023-10-27 15:31:00 - INFO - [RelayerSimulator] - Found 1 transaction(s) ready to be relayed.
    2023-10-27 15:31:00 - INFO - [RelayerSimulator] - Relaying transaction 0x1a2b3c... to chain 80001
    2023-10-27 15:31:01 - INFO - [RelayerSimulator] - Fetched gas price from external API: 35 Gwei
    2023-10-27 15:31:03 - INFO - [RelayerSimulator] - Transaction 0x1a2b3c... successfully submitted to destination. Simulated dest hash: 0x9f8e7d...
    2023-10-27 15:31:03 - INFO - [CrossChainTransaction] - Transaction 0x1a2b3c... updated to status: SUBMITTED_TO_DEST
    ...
    2023-10-27 15:32:05 - INFO - [main] - 
    --- Transaction Manager Summary ---
    > PENDING_CONFIRMATION: 0
    > READY_TO_RELAY: 0
    > SUBMITTED_TO_DEST: 1
    > FINALIZED: 0
    > FAILED: 0
    -----------------------------------
    ```
