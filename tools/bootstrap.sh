#!/usr/bin/env bash
# Install pinned tools inside this checkout. No sudo or shell-profile changes.
set -euo pipefail
tool_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_bin="$tool_root/.tools/bin"
tool_tmp="$(mktemp -d)"
trap 'rm -rf "$tool_tmp"' EXIT
mkdir -p "$tool_bin"

for tool_required in git rg curl tar sha256sum awk install; do
  if ! command -v "$tool_required" >/dev/null 2>&1; then
    printf 'Missing system tool: %s. Install the prerequisites listed in README.md.\n' "$tool_required" >&2
    exit 1
  fi
done

case "$(uname -s):$(uname -m)" in
  Linux:x86_64) tool_lk_arch=amd64; tool_uv_arch=x86_64 ;;
  Linux:aarch64) tool_lk_arch=arm64; tool_uv_arch=aarch64 ;;
  *) echo 'This bootstrap supports Linux x86_64/aarch64.' >&2; exit 1 ;;
esac

tool_lk_version=2.19.2
if [[ -x "$tool_bin/lk" ]] && [[ "$("$tool_bin/lk" --version)" == "lk version $tool_lk_version" ]]; then
  printf 'Reusing LiveKit CLI %s.\n' "$tool_lk_version"
else
  tool_lk_archive="lk_${tool_lk_version}_linux_${tool_lk_arch}.tar.gz"
  tool_lk_url="https://github.com/livekit/livekit-cli/releases/download/v${tool_lk_version}"
  curl -fsSL --retry 3 "$tool_lk_url/$tool_lk_archive" -o "$tool_tmp/$tool_lk_archive"
  curl -fsSL --retry 3 "$tool_lk_url/checksums.txt" -o "$tool_tmp/lk-checksums.txt"
  tool_lk_sha="$(awk -v name="$tool_lk_archive" '$2 == name {print $1}' "$tool_tmp/lk-checksums.txt")"
  test -n "$tool_lk_sha"
  printf '%s  %s\n' "$tool_lk_sha" "$tool_tmp/$tool_lk_archive" | sha256sum --check --status
  tar -xzf "$tool_tmp/$tool_lk_archive" -C "$tool_tmp" lk
  install -m 0755 "$tool_tmp/lk" "$tool_bin/lk"
fi

tool_uv_version=0.13.5
tool_uv_installed=""
if [[ -x "$tool_bin/uv" ]]; then
  tool_uv_installed="$("$tool_bin/uv" --version)"
fi
if [[ "$tool_uv_installed" == "uv $tool_uv_version" || "$tool_uv_installed" == "uv $tool_uv_version "* ]]; then
  printf 'Reusing uv %s.\n' "$tool_uv_version"
else
  tool_uv_target="uv-${tool_uv_arch}-unknown-linux-gnu"
  tool_uv_archive="$tool_uv_target.tar.gz"
  tool_uv_url="https://github.com/astral-sh/uv/releases/download/$tool_uv_version"
  curl -fsSL --retry 3 "$tool_uv_url/$tool_uv_archive" -o "$tool_tmp/$tool_uv_archive"
  curl -fsSL --retry 3 "$tool_uv_url/$tool_uv_archive.sha256" -o "$tool_tmp/uv-checksum.txt"
  tool_uv_sha="$(awk 'NR == 1 {print $1}' "$tool_tmp/uv-checksum.txt")"
  printf '%s  %s\n' "$tool_uv_sha" "$tool_tmp/$tool_uv_archive" | sha256sum --check --status
  tar -xzf "$tool_tmp/$tool_uv_archive" -C "$tool_tmp" "$tool_uv_target/uv"
  install -m 0755 "$tool_tmp/$tool_uv_target/uv" "$tool_bin/uv"
fi

"$tool_bin/lk" --version
"$tool_bin/uv" --version
printf 'Installed in %s\n' "$tool_bin"
