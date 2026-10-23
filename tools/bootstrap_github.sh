#!/usr/bin/env bash
# Optional pinned GitHub CLI for human-approved PR operations. No login or pushes.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version=2.103.0
case "$(uname -s):$(uname -m)" in
  Linux:x86_64) arch=amd64 ;;
  Linux:aarch64) arch=arm64 ;;
  *) echo 'GitHub CLI bootstrap supports Linux x86_64/aarch64.' >&2; exit 1 ;;
esac
if [[ -x "$root/.tools/bin/gh" ]] && [[ "$("$root/.tools/bin/gh" --version)" == "gh version $version "* ]]; then
  echo "Reusing GitHub CLI $version."
  exit 0
fi
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT
archive="gh_${version}_linux_${arch}.tar.gz"
release="https://github.com/cli/cli/releases/download/v${version}"
curl -fsSL --retry 3 "$release/$archive" -o "$temporary/$archive"
curl -fsSL --retry 3 "$release/gh_${version}_checksums.txt" -o "$temporary/checksums.txt"
checksum="$(awk -v name="$archive" '$2 == name {print $1}' "$temporary/checksums.txt")"
test -n "$checksum"
printf '%s  %s\n' "$checksum" "$temporary/$archive" | sha256sum --check --status
tar -xzf "$temporary/$archive" -C "$temporary" "gh_${version}_linux_${arch}/bin/gh"
install -m 0755 "$temporary/gh_${version}_linux_${arch}/bin/gh" "$root/.tools/bin/gh"
"$root/.tools/bin/gh" --version
echo 'Installed only. Human GitHub login is separate; no credentials were changed.'
