#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d /tmp/cal-bar-eds-test.XXXXXX)"
cleanup() {
    local result=$?
    # Activated GNOME services can finish writing just after the private bus exits.
    for attempt in {1..5}; do
        if rm -rf "$test_root" 2>/dev/null; then return "$result"; fi
        sleep 0.2
    done
    printf 'Warning: temporary test directory remains: %s\n' "$test_root" >&2
    return "$result"
}
trap cleanup EXIT
mkdir -m 700 "$test_root/runtime"
mkdir -p "$test_root/data" "$test_root/config" "$test_root/cache"
XDG_RUNTIME_DIR="$test_root/runtime" \
XDG_DATA_HOME="$test_root/data" \
XDG_CONFIG_HOME="$test_root/config" \
XDG_CACHE_HOME="$test_root/cache" \
CAL_BAR_EDS_TEST_ISOLATED=1 \
dbus-run-session -- /usr/bin/python3 "$repo_dir/tests/eds_integration.py"
