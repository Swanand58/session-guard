#!/bin/bash
# One-command install:
#   curl -fsSL https://raw.githubusercontent.com/Swanand58/session-guard/main/install.sh | bash
# Downloads session-guard to ~/.session-guard (running it again updates it; your .env is kept),
# then runs install.py.
set -euo pipefail
DIR="$HOME/.session-guard"
mkdir -p "$DIR"
curl -fsSL https://github.com/Swanand58/session-guard/archive/refs/heads/main.tar.gz | tar xz -C "$DIR" --strip-components=1
python3 "$DIR/install.py"
