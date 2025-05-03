# Use an official Python runtime as a parent image
FROM python:3.11-slim as builder

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install dependencies
# Copy only requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Final Stage ---
FROM python:3.11-slim

WORKDIR /app

# Copy installed dependencies from builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy the application code into the container
COPY relay_server.py .
COPY requirements.txt .
# Copy certificates (ensure they exist in the build context)
# Alternatively, mount these as volumes in docker-compose for easier management
COPY ssl_cert.pem .
COPY ssl_key.pem .

# Expose the UDP port the server listens on
EXPOSE 4433/udp
RUN pip install -r requirements.txt
# Define the command to run the application
CMD ["python", "relay_server.py"]
