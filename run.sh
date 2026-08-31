#!/usr/bin/env bash
set -e

if [ -n "$TAILSCALE_AUTHKEY" ]; then
    echo "[TAILSCALE] Downloading static binaries..."
    TS_VERSION="1.62.0"
    curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${TS_VERSION}_amd64.tgz" -o /tmp/tailscale.tgz
    mkdir -p /tmp/ts /tmp/ts-state /tmp/ts-run
    tar -C /tmp/ts -xzf /tmp/tailscale.tgz --strip-components=1
    export PATH="/tmp/ts:$PATH"

    echo "[TAILSCALE] Starting tailscaled userspace daemon in /tmp..."
    tailscaled \
        --tun=userspace-networking \
        --socket=/tmp/ts-run/tailscaled.sock \
        --state=/tmp/ts-state/tailscaled.state \
        --socks5-server=localhost:1055 \
        --outbound-http-proxy-listen=localhost:1055 &

    echo "[TAILSCALE] Connecting to tailnet..."
    until tailscale --socket=/tmp/ts-run/tailscaled.sock up --authkey="${TAILSCALE_AUTHKEY}" --hostname="render-dispute-api" --exit-node="${TAILSCALE_EXIT_NODE}"; do
        echo "[TAILSCALE] Waiting for daemon..."
        sleep 2
    done
    echo "[TAILSCALE] Connected successfully via exit node ${TAILSCALE_EXIT_NODE}."
fi

exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}
