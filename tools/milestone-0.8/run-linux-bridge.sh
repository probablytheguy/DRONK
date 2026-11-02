#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
"$repo_root/gradlew" -p "$repo_root" :linux-bridge:installDist
processor="$repo_root/linux-bridge/build/install/linux-bridge/bin/linux-bridge"
exec python3 "$repo_root/tools/milestone-0.8/linux_serial_bridge.py" --processor "$processor" "$@"
