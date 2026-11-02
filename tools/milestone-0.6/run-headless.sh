#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="$(mktemp -d /tmp/ouitak-m06-java.XXXXXX)"
trap 'rm -rf -- "$build_dir"' EXIT

: "${OUI_TAK_CLIENT_P12:?Set OUI_TAK_CLIENT_P12 to the existing protected PKCS#12 file}"
: "${OUI_TAK_CLIENT_PASSWORD_FILE:?Set OUI_TAK_CLIENT_PASSWORD_FILE to its protected password file}"
: "${OUI_TAK_CA_FILE:?Set OUI_TAK_CA_FILE to the trusted public CA bundle}"

javac --release 17 -d "$build_dir" "$repo_root/tools/milestone-0.6/HeadlessTakIntegration.java"
cd "$repo_root"
java -cp "$build_dir" HeadlessTakIntegration \
  --client-p12 "$OUI_TAK_CLIENT_P12" \
  --password-file "$OUI_TAK_CLIENT_PASSWORD_FILE" \
  --ca-file "$OUI_TAK_CA_FILE" \
  "$@"
