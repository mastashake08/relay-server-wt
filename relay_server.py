import asyncio
import logging
import json
import os
from typing import Dict, Optional, cast, Any, List, Tuple

# Import necessary AIOQUIC components
from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent, StreamDataReceived, ConnectionTerminated, HandshakeCompleted, ProtocolNegotiated, DatagramFrameReceived, StreamReset
from aioquic.quic.connection import QuicConnection
from aioquic.quic.packet import QuicErrorCode
from aioquic.quic.stream import StreamFinishedError
from aioquic.quic.logger import QuicLogger

# Import HTTP/3 and WebTransport related components
from aioquic.h3.connection import H3Connection, Setting
from aioquic.h3.events import H3Event, DataReceived, HeadersReceived, WebTransportStreamDataReceived, DatagramReceived as H3DatagramReceived
from aioquic.h3.exceptions import NoAvailablePushIDError
from urllib.parse import urlparse

# Import Session Ticket Handling (remains the same)


# --- Configuration ---
HOST = "::"
PORT = 4433
CERT_FILE = "ssl_cert.pem"
KEY_FILE = "ssl_key.pem"
LOG_LEVEL = logging.INFO
WEBTRANSPORT_PATH = "/relay" # Path clients connect to for WebTransport

# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("relay_server")

# --- Shared State ---
# Dictionary to hold active client connections: { client_id: protocol }
CLIENTS: Dict[str, QuicConnectionProtocol] = {}

# Simple in-memory session ticket store
#

# --- Session Ticket Handling (Unchanged) ---

# --- Server Protocol with H3/WebTransport ---
class RelayServerProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client_id: Optional[str] = None
        self._peer_addr: Optional[tuple] = None
        self._http: Optional[H3Connection] = None # H3 connection instance
        self._webtransport_session_id: Optional[int] = None # Stream ID of the establishing CONNECT request

    def connection_made(self, transport) -> None:
        super().connection_made(transport)
        self._peer_addr = transport.get_extra_info("peername")
        logger.debug(f"Connection attempt from {self._peer_addr}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        client_id_log = self._client_id or "unknown"
        logger.info(f"Client {client_id_log} disconnected from {self._peer_addr} (Reason: {exc})")
        if self._client_id and self._client_id in CLIENTS:
            del CLIENTS[self._client_id]
            logger.info(f"Removed client {self._client_id}. Remaining clients: {list(CLIENTS.keys())}")
        else:
            logger.warning(f"Connection lost for unregistered or already removed client {client_id_log} from {self._peer_addr}")
        super().connection_lost(exc) # Ensure parent cleanup runs

    # --- QUIC Event Handling ---
    def quic_event_received(self, event: QuicEvent) -> None:
        # logger.debug(f"QUIC Event from {self._client_id or 'unknown'}: {event}") # Verbose

        if isinstance(event, ProtocolNegotiated):
            # Check if HTTP/3 was negotiated
            if event.alpn_protocol and event.alpn_protocol.startswith("h3"):
                logger.info(f"HTTP/3 negotiated with {self._peer_addr} (ALPN: {event.alpn_protocol})")
                self._http = H3Connection(
                    self._quic,
                    enable_webtransport=True, # Explicitly enable WebTransport
                    enable_datagram=True # Enable datagram support
                )
            else:
                logger.info(f"Non-HTTP/3 protocol negotiated: {event.alpn_protocol}. Using raw QUIC streams.")
                # Proceed without H3 connection for raw QUIC clients

        elif isinstance(event, HandshakeCompleted):
            # Assign Client ID here, works for both H3 and raw QUIC
            conn_id = self._quic.original_destination_connection_id
            new_client_id = conn_id.hex()
            logger.info(f"Handshake complete for {self._peer_addr}. Client ID: {new_client_id}")

            if new_client_id in CLIENTS and CLIENTS[new_client_id] is not self:
                 logger.warning(f"Client ID collision: {new_client_id}. Closing new connection from {self._peer_addr}.")
                 self.close(error_code=QuicErrorCode.INTERNAL_ERROR, reason_phrase="Client ID collision")
                 return

            if self._client_id and self._client_id != new_client_id and self._client_id in CLIENTS:
                 logger.warning(f"Client {self._peer_addr} reconnected with new ID. Old ID: {self._client_id}, New ID: {new_client_id}")
                 del CLIENTS[self._client_id] # Remove old entry

            self._client_id = new_client_id
            CLIENTS[self._client_id] = self
            logger.info(f"Client registered: {self._client_id} ({self._peer_addr})")
            logger.info(f"Current clients: {list(CLIENTS.keys())}")
            # Don't send ID here yet, wait for WebTransport session or first raw stream data

        elif isinstance(event, StreamDataReceived):
            if self._http:
                # If H3 is active, pass stream data to H3 connection
                # This includes data for the CONNECT request and subsequent WebTransport streams
                try:
                    for h3_event in self._http.handle_event(event):
                        self._h3_event_received(h3_event)
                except Exception as e:
                    logger.error(f"H3 Stream Error handling data from {self._client_id} on stream {event.stream_id}: {e}", exc_info=True)
                    # Consider closing connection or specific stream based on error severity
            else:
                # Handle as raw QUIC stream data (original logic)
                self._handle_raw_quic_data(event.data, event.stream_id)

        elif isinstance(event, DatagramFrameReceived):
             if self._http:
                 # Pass datagrams to H3 connection if active
                 try:
                     for h3_event in self._http.handle_event(event):
                         self._h3_event_received(h3_event)
                 except Exception as e:
                    logger.error(f"H3 Datagram Error handling data from {self._client_id}: {e}", exc_info=True)
             else:
                 # Handle raw QUIC datagrams if needed (optional)
                 logger.debug(f"Received raw QUIC datagram from {self._client_id}: {event.data}")
                 # Add raw datagram relay logic here if required

        elif isinstance(event, ConnectionTerminated):
            logger.warning(f"QUIC Connection terminated for {self._client_id or self._peer_addr}: code={event.error_code}, phrase='{event.reason_phrase}', type={event.frame_type}")
            # connection_lost handles cleanup

        elif isinstance(event, StreamReset):
             logger.warning(f"Stream {event.stream_id} reset by client {self._client_id}: code={event.error_code}")
             if self._http:
                 try:
                     for h3_event in self._http.handle_event(event):
                         self._h3_event_received(h3_event)
                 except Exception as e:
                     logger.error(f"Error handling stream reset for H3: {e}")


        # Pass other relevant QUIC events to H3 connection if it exists
        if self._http and hasattr(self._http, "handle_event"):
             try:
                for h3_event in self._http.handle_event(event):
                    self._h3_event_received(h3_event)
             except Exception as e:
                 # Log errors during general event handling for H3
                 logger.error(f"Error passing QUIC event {type(event)} to H3 connection: {e}", exc_info=True)


    # --- H3 Event Handling ---
    def _h3_event_received(self, event: H3Event) -> None:
        # logger.debug(f"H3 Event from {self._client_id}: {event}") # Verbose

        if isinstance(event, HeadersReceived):
            logger.debug(f"H3 HeadersReceived from {self._client_id} on stream {event.stream_id}: {event.headers}")
            headers = dict(event.headers)
            method = headers.get(b":method", b"").decode()
            path = headers.get(b":path", b"").decode()
            protocol = headers.get(b":protocol", b"").decode()
            origin = headers.get(b"origin") # For security checks
            logger.info(f"H3 Request from {self._client_id}: {method} {path} (Stream: {event.stream_id}, Protocol: {protocol})")

            # Check if it's a WebTransport request
            if method == "CONNECT" and protocol == "webtransport" and path == WEBTRANSPORT_PATH:
                # Validate Origin header if needed for security
                # if not origin or origin.decode() != "expected.origin.com":
                #     logger.warning(f"WebTransport request rejected from {self._client_id}: Invalid origin {origin}")
                #     self._http.send_headers(event.stream_id, [(b":status", b"403")], end_stream=True)
                #     self.transmit()
                #     return

                logger.info(f"Accepting WebTransport session from {self._client_id} on stream {event.stream_id}")
                self._webtransport_session_id = event.stream_id # Store the control stream ID

                # Send 200 OK response to establish the session
                self._http.send_headers(event.stream_id, [
                    (b":status", b"200"),
                    (b"sec-webtransport-http3-draft", b"draft02") # Required header
                ])
                self.transmit() # Ensure headers are sent

                # Now that session is established, send the client their ID
                self.send_message_to_client({"type": "your_id", "id": self._client_id})

            else:
                # Handle other H3 requests (e.g., GET /) or reject
                logger.warning(f"Unhandled H3 request from {self._client_id}: {method} {path}")
                self._http.send_headers(event.stream_id, [(b":status", b"404")], end_stream=True)
                self.transmit()

        elif isinstance(event, WebTransportStreamDataReceived):
            # Data received on a WebTransport data stream (not the control stream)
            logger.debug(f"WebTransport data from {self._client_id} on session {event.session_id}, stream {event.stream_id}: {event.data}")
            if event.session_id == self._webtransport_session_id:
                self._handle_webtransport_data(event.data, event.stream_id, event.stream_ended)
            else:
                 logger.warning(f"Received WebTransport data for unknown session {event.session_id}")

        elif isinstance(event, DataReceived):
             # Data received on the H3 control stream (e.g., for the initial CONNECT)
             # Or potentially other non-WebTransport H3 streams if supported
             logger.debug(f"H3 DataReceived from {self._client_id} on stream {event.stream_id}: {event.data}")
             # If this is the WebTransport control stream, you might handle specific messages here
             if event.stream_id == self._webtransport_session_id:
                 logger.warning(f"Data received on WebTransport control stream {event.stream_id} - usually not expected after setup.")
                 # Handle control messages if your application defines them
             # else: handle other H3 stream data

        elif isinstance(event, H3DatagramReceived):
            logger.debug(f"H3 Datagram received from {self._client_id} (flow_id {event.flow_id}): {event.data}")
            # Handle WebTransport datagrams if needed
            # You might need to map flow_id to recipient or parse data for destination
            # self._handle_webtransport_datagram(event.data, event.flow_id)




    # --- Data Handling Logic ---

    def _handle_raw_quic_data(self, data: bytes, stream_id: int):
        """Handles data received on a raw QUIC stream (non-H3)."""
        logger.debug(f"Handling raw QUIC data from {self._client_id} on stream {stream_id}: {data}")
        if not self._client_id:
            logger.warning(f"Received raw QUIC data from {self._peer_addr} before ID assigned. Ignoring.")
            return

        # Send ID on first data if not sent already (alternative to HandshakeCompleted)
        # if not self._id_sent: # Add a flag _id_sent if using this approach
        #    self.send_message_to_client({"type": "your_id", "id": self._client_id})
        #    self._id_sent = True

        try:
            message = json.loads(data.decode('utf-8'))
            self._process_and_relay_message(message)
        except json.JSONDecodeError:
            logger.warning(f"Received non-JSON raw QUIC data from {self._client_id} on stream {stream_id}. Ignoring.")
            self.send_error_to_client("Invalid data format: Expected JSON.")
        except UnicodeDecodeError:
            logger.warning(f"Received non-UTF8 raw QUIC data from {self._client_id} on stream {stream_id}. Ignoring.")
            self.send_error_to_client("Invalid data encoding: Expected UTF-8.")
        except Exception as e:
            logger.error(f"Error processing raw QUIC data from {self._client_id}: {e}", exc_info=True)
            self.send_error_to_client("Internal server error processing your message.")

    def _handle_webtransport_data(self, data: bytes, stream_id: int, stream_ended: bool):
        """Handles data received on a WebTransport stream."""
        if not self._client_id: return # Should have ID by now

        try:
            message = json.loads(data.decode('utf-8'))
            self._process_and_relay_message(message)

            # Acknowledge received data on the stream if it hasn't ended yet
            # This is important for flow control in WebTransport
            if not stream_ended and self._http:
                 try:
                     self._http.stream_data_received(stream_id=stream_id, length=len(data))
                     self.transmit()
                 except StreamFinishedError:
                     logger.debug(f"Stream {stream_id} already finished when trying to acknowledge data.")
                 except Exception as e:
                     logger.error(f"Error acknowledging WT stream data for {stream_id}: {e}")


        except json.JSONDecodeError:
            logger.warning(f"Received non-JSON WebTransport data from {self._client_id} on stream {stream_id}. Ignoring.")
            self.send_error_to_client("Invalid data format: Expected JSON.")
        except UnicodeDecodeError:
            logger.warning(f"Received non-UTF8 WebTransport data from {self._client_id} on stream {stream_id}. Ignoring.")
            self.send_error_to_client("Invalid data encoding: Expected UTF-8.")
        except Exception as e:
            logger.error(f"Error processing WebTransport data from {self._client_id}: {e}", exc_info=True)
            self.send_error_to_client("Internal server error processing your message.")

        # If stream ended, log it (no specific action needed here unless tracking stream states)
        if stream_ended:
            logger.debug(f"WebTransport stream {stream_id} ended by client {self._client_id}.")


    def _process_and_relay_message(self, message: dict):
        """Common logic to process parsed message and relay it."""
        if not self._client_id: return

        recipient_id = message.get("to")
        payload = message.get("payload")

        if not recipient_id or payload is None:
            logger.warning(f"Invalid message format from {self._client_id}: {message}")
            self.send_error_to_client(f"Invalid message format. Required fields: 'to', 'payload'.")
            return

        # Prepare message to forward
        message_to_forward = {
            "from": self._client_id,
            "payload": payload
        }
        encoded_data = json.dumps(message_to_forward).encode('utf-8')

        # Find recipient protocol
        recipient_protocol = CLIENTS.get(recipient_id)

        if recipient_protocol and recipient_protocol._quic and not recipient_protocol._quic.is_closed:
            logger.info(f"Relaying message from {self._client_id} to {recipient_id}")
            success = recipient_protocol.send_message_to_client(message_to_forward)
            if not success:
                logger.error(f"Failed to relay message to {recipient_id}.")
                # Send error back to original sender
                self.send_error_to_client(f"Failed to send message: Recipient {recipient_id} connection error.")
                # Optionally remove recipient if sending failed consistently
                # if recipient_id in CLIENTS: del CLIENTS[recipient_id]
        else:
            logger.warning(f"Recipient '{recipient_id}' not found or connection closed for message from {self._client_id}")
            self.send_error_to_client(f"Recipient '{recipient_id}' not found or offline.")


    # --- Helper methods ---
    def send_message_to_client(self, message: dict) -> bool:
        """Sends a JSON message to the client associated with this protocol, using WebTransport/H3 if available."""
        if not self._client_id or not self._quic or self._quic.is_closed:
            logger.warning(f"Cannot send message to {self._client_id or 'unknown'}: Connection closed or not ready.")
            return False

        try:
            data = json.dumps(message).encode('utf-8')

            if self._http and self._webtransport_session_id is not None:
                # Send via WebTransport/H3 stream
                try:
                    # Create a new unidirectional stream for sending the message
                    stream_id = self._http.create_stream(is_unidirectional=True)
                    logger.debug(f"Sending WebTransport message to {self._client_id} on new stream {stream_id}")
                    self._http.send_data(stream_id, data, end_stream=True)
                    self.transmit()
                    return True
                except NoAvailablePushIDError:
                     logger.warning(f"No available push ID to create stream for {self._client_id}. Is connection closing?")
                     return False
                except Exception as e:
                     logger.error(f"Failed to send WebTransport message to {self._client_id} on stream {stream_id}: {e}", exc_info=True)
                     return False
            else:
                # Send via raw QUIC stream (fallback)
                logger.debug(f"Sending raw QUIC message to {self._client_id}")
                stream_id = self._quic.get_next_available_stream_id(is_unidirectional=False) # Or True
                self._quic.send_stream_data(stream_id, data, end_stream=True)
                self.transmit()
                return True

        except ConnectionError as e:
             logger.error(f"Connection error sending message to {self._client_id}: {e}")
             return False
        except Exception as e:
            logger.error(f"Generic error sending message to {self._client_id}: {e}", exc_info=True)
            return False

    def send_error_to_client(self, error_message: str) -> None:
        """Sends an error message back to the client."""
        logger.debug(f"Sending error to {self._client_id}: {error_message}")
        self.send_message_to_client({"type": "error", "message": error_message})


# --- Main Server Setup ---
async def main(host=HOST, port=PORT, cert_file=CERT_FILE, key_file=KEY_FILE):
    logger.info("Starting QUIC relay server with WebTransport support...")

    if not os.path.exists(cert_file) or not os.path.exists(key_file):
        logger.error(f"Certificate ({cert_file}) or Key ({key_file}) not found.")
        logger.error("Generate with: openssl req -x509 -newkey rsa:4096 -nodes -keyout ssl_key.pem -out ssl_cert.pem -days 365 -subj \"/CN=localhost\"")
        return

    # Create QUIC configuration
    configuration = QuicConfiguration(
        is_client=False,
        # Offer h3 for WebTransport and raw protocol (e.g., 'relay') for non-browser clients
        alpn_protocols=["h3", "h3-34", "h3-33", "h3-32", "h3-29", "relay-protocol"], # Add your raw protocol ALPN if needed
        max_datagram_frame_size=65536,
        quic_logger=QuicLogger() if LOG_LEVEL <= logging.DEBUG else None # Enable detailed QUIC logging if DEBUG
    )
    configuration.load_cert_chain(cert_file, key_file)

    # Enable H3 settings needed for WebTransport and Datagrams
    configuration.h3_settings = {
         Setting.H3_DATAGRAM: 1, # Enable H3 datagrams
         Setting.ENABLE_WEBTRANSPORT: 1, # Enable WebTransport
         # Setting.WEBTRANSPORT_MAX_SESSIONS: 64, # Optional: Limit concurrent sessions
    }


    # Set up session ticket handler
    #ticket_handler = SimpleSessionTicketHandler()

    try:
        logger.info(f"Listening on {host}:{port} (UDP) for QUIC, H3, WebTransport ({WEBTRANSPORT_PATH})")
        await serve(
            host,
            port,
            configuration=configuration,
            create_protocol=RelayServerProtocol,
           # session_ticket_handler=ticket_handler,
            retry=True,
        )
        await asyncio.Future() # Keep server running
    except OSError as e:
        logger.error(f"Failed to bind to {host}:{port} - {e}")
    except Exception as e:
        logger.error(f"Server failed to start: {e}", exc_info=True)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Server stopped manually.")
    finally:
        # Perform any final cleanup if needed before closing loop
        loop.close()
        logger.info("Event loop closed.")

