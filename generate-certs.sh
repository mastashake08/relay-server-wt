#!/bin/bash

# Script to generate self-signed certificates and calculate the WebTransport hash.

# --- Configuration ---
KEY_FILE="ssl_key.pem"
CERT_FILE="ssl_cert.pem"
DAYS_VALID=365
# Subject line for the certificate (adjust CN=localhost if needed)
SUBJECT="/CN=localhost"
# Use a strong key algorithm and size
KEY_ALG="rsa:4096"

# --- Functions ---
cleanup() {
  echo "Cleaning up generated files..."
  rm -f "$KEY_FILE" "$CERT_FILE"
  exit 1
}

# Trap errors and Ctrl+C for cleanup
trap cleanup ERR INT TERM

# --- Main Script ---
echo "Generating self-signed certificate and private key..."

# Generate private key and certificate request, then sign it
# -x509: Output a self-signed certificate instead of a certificate request.
# -newkey ${KEY_ALG}: Generate a new private key using the specified algorithm.
# -nodes: Don't encrypt the private key (no passphrase). Remove if you want a passphrase.
# -keyout ${KEY_FILE}: Specifies the output file for the private key.
# -out ${CERT_FILE}: Specifies the output file for the certificate.
# -days ${DAYS_VALID}: Sets the validity period of the certificate.
# -subj "${SUBJECT}": Sets the subject field directly, avoiding interactive prompts.
openssl req -x509 \
            -newkey "${KEY_ALG}" \
            -nodes \
            -keyout "$KEY_FILE" \
            -out "$CERT_FILE" \
            -days "$DAYS_VALID" \
            -subj "$SUBJECT" \
            -addext "subjectAltName = DNS:localhost" # Add SAN for modern browser compatibility

# Check if certificate generation was successful
if [ ! -f "$CERT_FILE" ]; then
    echo "Error: Certificate file '$CERT_FILE' was not created."
    exit 1
fi

echo "Certificate created: $CERT_FILE"
echo "Private key created: $KEY_FILE"
echo ""
echo "Calculating SHA-256 certificate hash (Base64 encoded) for WebTransport..."

# Calculate the SHA-256 hash of the certificate in DER format and Base64 encode it
# -in ${CERT_FILE}: Input certificate file.
# -outform der: Output the certificate in DER (binary) format.
# | openssl base64 -A: Pipe the DER output to base64 encode it. -A puts it all on one line.
CERT_HASH=$(openssl x509 -in "$CERT_FILE" -outform der | openssl base64 -A)

# Check if hash calculation was successful
if [ -z "$CERT_HASH" ]; then
    echo "Error: Failed to calculate certificate hash."
    cleanup # Clean up files if hash calculation fails
fi

echo "---------------------------------------------------------------------"
echo "WebTransport serverCertificateHashes value (sha-256):"
echo ""
echo "$CERT_HASH"
echo ""
echo "---------------------------------------------------------------------"
echo "Use this value in your WebTransport client configuration."
echo "Script finished successfully."

# Untrap signals before exiting normally
trap - ERR INT TERM
exit 0
