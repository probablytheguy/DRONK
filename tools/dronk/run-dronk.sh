#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
"$repo_root/gradlew" -p "$repo_root" :linux-bridge:installDist
exec python3 "$repo_root/tools/dronk/command_center.py" "$@"
