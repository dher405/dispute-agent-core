FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    iptables \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN TS_VERSION="1.62.0" && \
    curl -fsSL https://pkgs.tailscale.com/stable/tailscale_${TS_VERSION}_amd64.tgz -o tailscale.tgz && \
    tar -C /usr/local/bin -xzf tailscale.tgz --strip-components=1 tailscale_${TS_VERSION}_amd64/tailscale tailscale_${TS_VERSION}_amd64/tailscaled && \
    rm tailscale.tgz

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x start.sh

EXPOSE 10000

CMD ["./start.sh"]
