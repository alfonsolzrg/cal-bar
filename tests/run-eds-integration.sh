#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d /tmp/cal-bar-eds-test.XXXXXX)"
trap 'rm -rf "$test_root"' EXIT
mkdir -m 700 "$test_root/runtime"
mkdir -p "$test_root/data" "$test_root/config" "$test_root/cache"
XDG_RUNTIME_DIR="$test_root/runtime" \
XDG_DATA_HOME="$test_root/data" \
XDG_CONFIG_HOME="$test_root/config" \
XDG_CACHE_HOME="$test_root/cache" \
CAL_BAR_EDS_TEST_ISOLATED=1 \
dbus-run-session -- /usr/bin/python3 "$repo_dir/tests/eds_integration.py"
