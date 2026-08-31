#!/usr/bin/env bash
set -e

if [ -n "$TAILSCALE_AUTHKEY" ]; then
    (
        echo "[TAILSCALE] Initializing background network layer..."
        TS_VERSION="1.62.0"
        if [ ! -f /tmp/ts/tailscaled ]; then
            curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${TS_VERSION}_amd64.tgz" -o /tmp/tailscale.tgz
            mkdir -p /tmp/ts /tmp/ts-run
            tar -C /tmp/ts -xzf /tmp/tailscale.tgz --strip-components=1
        fi
        export PATH="/tmp/ts:$PATH"

        tailscaled \
            --tun=userspace-networking \
            --socket=/tmp/ts-run/tailscaled.sock \
            --state=mem: \
            --socks5-server=localhost:1055 \
            --outbound-http-proxy-listen=localhost:1055 >/dev/null 2>&1 &

        # Give daemon 3 seconds to spin up client socket before calling CLI
        sleep 3

        tailscale --socket=/tmp/ts-run/tailscaled.sock up \
            --authkey="${TAILSCALE_AUTHKEY}" \
            --hostname="render-dispute-api" \
            --exit-node="${TAILSCALE_EXIT_NODE}" \
            --accept-routes=false || true

        echo "[TAILSCALE] Background routine initialized."
    ) &
fi

# Launch Uvicorn immediately so Render passes port check
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}
