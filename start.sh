#!/usr/bin/env bash
set -e

if [ -n "$TAILSCALE_AUTHKEY" ]; then
    echo "[TAILSCALE] Starting tailscaled userspace daemon..."
    tailscaled --tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055 &
    
    until tailscale up --authkey="${TAILSCALE_AUTHKEY}" --hostname="render-dispute-api" --exit-node="${TAILSCALE_EXIT_NODE}"; do
        echo "[TAILSCALE] Waiting for tailscale daemon..."
        sleep 2
    done
    echo "[TAILSCALE] Tailscale connected and routing via exit node ${TAILSCALE_EXIT_NODE}."
fi

exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}
