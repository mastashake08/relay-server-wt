# AIOQUIC Relay Server (with WebTransport)

## Description

This project implements a simple message relay server using Python's `aioquic` library. It allows multiple clients to connect and send messages to each other through the server. The server supports connections via both:

1.  **Raw QUIC:** For custom clients directly using the QUIC protocol.
2.  **WebTransport:** Allows browser-based applications to connect using the WebTransport API over HTTP/3.

The server manages client connections and forwards JSON messages based on a `to` field indicating the recipient's connection ID.

## Features

* **Message Relaying:** Forwards JSON messages between connected clients.
* **Dual Protocol Support:** Accepts connections via raw QUIC streams and WebTransport over HTTP/3.
* **Session Resumption:** Basic in-memory session ticket handling to speed up client reconnections.
* **Certificate Hash Validation:** Supports clients connecting via WebTransport using `serverCertificateHashes` for security.
* **Docker & Dev Container Support:** Includes `Dockerfile`, `docker-compose.yml`, and Dev Container configuration (`.devcontainer/devcontainer.json`) for easy setup and development.
* **Automatic Certificate Generation:** Provides a script (`generate_certs.sh`) to create self-signed certificates and the required WebTransport hash.

## Prerequisites

* **Docker:** Docker Desktop (Windows/Mac) or Docker Engine (Linux) must be installed and running. [Install Docker](https://docs.docker.com/engine/install/)
* **VS Code (Recommended for Dev Container):** Visual Studio Code. [Install VS Code](https://code.visualstudio.com/)
* **VS Code Remote - Containers Extension (Recommended):** For using the Dev Container. Install from the VS Code Marketplace (ID: `ms-vscode-remote.remote-containers`).

## Setup and Running (Using Dev Container - Recommended)

1.  **Clone the Repository:** Get the project files onto your local machine.
2.  **Open in VS Code:** Open the project folder in Visual Studio Code.
3.  **Reopen in Container:** VS Code should prompt you to "Reopen in Container". Click it. (Alternatively, use the Command Palette: `Remote-Containers: Reopen in Container`).
4.  **Wait for Build:** The Dev Container will build (first time) and start. Dependencies (`requirements.txt`) will be installed automatically.
5.  **Generate Certificates:** The `postStartCommand` in `devcontainer.json` will automatically run `./generate_certs.sh` if `ssl_cert.pem` or `ssl_key.pem` are not found. This creates the necessary certificates and prints the **WebTransport certificate hash** to the terminal. Copy this hash - you'll need it for WebTransport clients.
6.  **Run the Server:** Open the integrated terminal in VS Code (which is now inside the container) and run:
    ```bash
    python relay_server.py
    ```
    The server will start listening on UDP port 4433.

## Setup and Running (Using Docker Compose)

1.  **Clone the Repository:** Get the project files.
2.  **Generate Certificates:** Run the certificate generation script manually first:
    ```bash
    chmod +x generate_certs.sh
    ./generate_certs.sh
    ```
    Copy the **WebTransport certificate hash** printed at the end.
3.  **Build and Run:** Use Docker Compose to build the image and run the container:
    ```bash
    docker-compose up --build
    ```
    The server will start in the container, listening on UDP port 4433. Press `Ctrl+C` to stop. Use `docker-compose down` to remove the container.

## Client Connection

* **WebTransport Clients:**
    * Connect to: `https://<server_ip_or_hostname>:4433/relay` (Use `localhost` if running locally).
    * **Crucially**, provide the `serverCertificateHashes` option during connection, using the Base64 SHA-256 hash obtained from `./generate_certs.sh`.
    * Example (JavaScript):
        ```javascript
        const certificateHash = 'PASTE_YOUR_BASE64_HASH_HERE';
        const transport = new WebTransport('https://localhost:4433/relay', {
            serverCertificateHashes: [{
                algorithm: 'sha-256',
                value: certificateHash // Must match the server's cert hash
            }]
        });
        // ... handle connection, sending/receiving JSON messages ...
        ```
* **Raw QUIC Clients:**
    * Connect directly to `<server_ip_or_hostname>:4433`.
    * Ensure the client negotiates an ALPN protocol *other than* `h3` (e.g., `relay-protocol` if you configured one in `relay_server.py`, otherwise it might default to raw QUIC streams if no ALPN matches).
    * Clients need to handle certificate validation (e.g., trusting the self-signed certificate or using a proper CA).

* **Client Identification:** The server currently uses the QUIC connection's original destination connection ID (hex format) as the client ID. It sends this ID to the client in a `{"type": "your_id", "id": "..."}` message upon successful connection (after handshake/WebTransport setup). Clients need this ID to address messages to others.
* **Message Format:** Clients should send JSON messages like:
    ```json
    {
      "to": "recipient_client_id_hex",
      "payload": { "your": "data" } // Payload can be any JSON-serializable data
    }
    ```
    Messages received by clients will look like:
    ```json
    {
      "from": "sender_client_id_hex",
      "payload": { "the": "data" }
    }
    ```

## File Structure

.├── .devcontainer/│   └── devcontainer.json   # VS Code Dev Container configuration├── docker-compose.yml      # Docker Compose file for running the server├── Dockerfile              # Defines the Docker image for the server├── generate_certs.sh       # Script to create certs and WebTransport hash├── relay_server.py         # The main Python AIOQUIC server code├── requirements.txt        # Python dependencies├── ssl_cert.pem            # Generated server certificate (public)├── ssl_key.pem             # Generated server private key└── README.md               # This file
## Future Improvements

* **Persistent Session Store:** Replace the in-memory `SessionTicketStore` with Redis, a database, or file storage for persistence across server restarts.
* **Application-Level Usernames:** Implement a proper login/registration system instead of relying on volatile connection IDs.
* **Datagram Relay:** Fully implement relaying for QUIC/WebTransport datagrams.
* **Robust Error Handling:** Add more specific error codes and potentially retry mechanisms.
* **Scalability:** Consider strategies for handling a larger number of connections if needed.
* **Testing:** Add unit and integration tests (e.g., using `pytest`).

