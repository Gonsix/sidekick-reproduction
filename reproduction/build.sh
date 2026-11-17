#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -s reproduction/cert.pem || ! -s reproduction/key.pem ]]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=localhost \
        -keyout reproduction/key.pem -out reproduction/cert.pem
    chmod 600 reproduction/key.pem
fi
cargo build --locked --release -p quiche \
    --example sidekick_transfer --example sidekick_proxy --example sidekick_media
cargo test --locked -p quiche --lib
