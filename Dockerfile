FROM python:3.11-slim-bookworm

# Install system dependencies: Libreswan (IPsec), xl2tpd, ppp, iptables, supervisor, networking tools
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libreswan \
        xl2tpd \
        ppp \
        iptables \
        iproute2 \
        procps \
        curl \
        ca-certificates \
        supervisor \
        kmod && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Setup supervisor and entrypoint
RUN mkdir -p /etc/supervisor/conf.d && \
    cp supervisord.conf /etc/supervisor/conf.d/supervisord.conf && \
    chmod +x /app/entrypoint.sh

# Environment variables defaults
ENV PORT=5000
ENV BIND_HOST=0.0.0.0
ENV DATABASE_PATH=/app/data/node.db
ENV CHAP_SECRETS_PATH=/app/data/chap-secrets
ENV DATA_DIR=/app/data

EXPOSE 5000
EXPOSE 500/udp
EXPOSE 4500/udp
EXPOSE 1701/udp

ENTRYPOINT ["/app/entrypoint.sh"]
