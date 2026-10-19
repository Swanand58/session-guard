#!/bin/bash
# One-command install (the latest release):
#   curl -fsSL https://raw.githubusercontent.com/Swanand58/session-guard/main/install.sh | bash
# A specific version: SESSION_GUARD_REF is a tag or a branch. Without it, the latest release is used;
# with no release yet, or if the lookup fails (rate limit, no network), the code on main is used.
#   curl -fsSL https://raw.githubusercontent.com/Swanand58/session-guard/main/install.sh | SESSION_GUARD_REF=v0.1.0 bash
# Downloads session-guard to ~/.session-guard (running it again updates it; your .env is kept),
# then runs install.py. Extra arguments go to install.py, so this also removes it again:
#   curl -fsSL https://raw.githubusercontent.com/Swanand58/session-guard/main/install.sh | bash -s -- uninstall --purge
set -euo pipefail
REPO="Swanand58/session-guard"
DIR="$HOME/.session-guard"
REF="${SESSION_GUARD_REF:-}"
if [ -z "$REF" ]; then
  # The latest release tag. Any failure (no release yet, rate limit, no network, odd answer) means main.
  REF=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null) || REF=""
  REF="${REF:-main}"
fi
case "$REF" in
  *[!A-Za-z0-9._/-]* | .* | -*) echo "session-guard: '$REF' is not a valid tag or branch name." >&2; exit 1 ;;
esac
mkdir -p "$DIR"
# archive/<ref>.tar.gz works for tags and branches alike.
curl -fsSL "https://github.com/$REPO/archive/$REF.tar.gz" | tar xz -C "$DIR" --strip-components=1 ||
  { echo "session-guard: could not download '$REF'. Check the name and your network." >&2; exit 1; }
echo "session-guard: downloaded $REF to $DIR"
python3 "$DIR/install.py" "$@"
