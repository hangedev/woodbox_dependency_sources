#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="$project_dir/tool/dependency_sources.json"
sdk_url=""
sdk_ref=""
sdk_version=""
cache_available=""
cache_os=""
cache_arch=""
while IFS=$'\t' read -r key value; do
  case "$key" in
    url) sdk_url="$value" ;;
    ref) sdk_ref="$value" ;;
    version) sdk_version="$value" ;;
    cache_available) cache_available="$value" ;;
    cache_os) cache_os="$value" ;;
    cache_arch) cache_arch="$value" ;;
  esac
done < <(python3 - "$manifest" <<'PY'
import json, sys
sdk = json.load(open(sys.argv[1]))['flutter_sdk']
for key in ['url', 'ref', 'version']:
    print(key + '\t' + sdk[key])
cache = sdk.get('cache', {})
for key, value in [('cache_available', '1' if cache.get('url') or cache.get('parts') else None),
                   ('cache_os', cache.get('os')), ('cache_arch', cache.get('arch'))]:
    if value:
        print(key + '\t' + value)
PY
)
[[ "$sdk_url" == https://github.com/hangedev/* && "$sdk_ref" =~ ^[0-9a-f]{40}$ ]] || {
  echo "Invalid preserved Flutter SDK reference." >&2
  exit 1
}

sdk_dir="${WOODBOX_FLUTTER_HOME:-$HOME/.cache/woodbox/flutter-$sdk_ref}"
if [[ ! -d "$sdk_dir/.git" ]]; then
  if [[ -e "$sdk_dir" ]]; then
    echo "SDK destination already exists without a Git checkout: $sdk_dir" >&2
    exit 1
  fi
  mkdir -p "$sdk_dir"
  git -C "$sdk_dir" init >&2
  git -C "$sdk_dir" remote add origin "$sdk_url"
fi
if ! git -C "$sdk_dir" rev-parse --verify HEAD >/dev/null 2>&1; then
  git -C "$sdk_dir" fetch --depth 1 origin "$sdk_ref" >&2
  git -C "$sdk_dir" checkout --detach FETCH_HEAD >&2
fi
[[ "$(git -C "$sdk_dir" rev-parse HEAD)" == "$sdk_ref" ]] || {
  echo "SDK checkout differs from the preserved revision: $sdk_dir" >&2
  exit 1
}

# Restore the version/commit pair from the manifest for shallow version detection.
if ! git -C "$sdk_dir" rev-parse --verify "refs/tags/$sdk_version" >/dev/null 2>&1; then
  git -C "$sdk_dir" tag "$sdk_version" "$sdk_ref"
fi
[[ "$(git -C "$sdk_dir" rev-list -n 1 "refs/tags/$sdk_version")" == "$sdk_ref" ]] || {
  echo "SDK version tag differs from the preserved revision." >&2
  exit 1
}

if [[ ! -x "$sdk_dir/bin/cache/dart-sdk/bin/dart" && "$cache_available" == 1 ]]; then
  if [[ "$(uname -s)" != "$cache_os" || "$(uname -m)" != "$cache_arch" ]]; then
    echo "The preserved SDK cache targets $cache_os/$cache_arch; this host will use Flutter's platform downloads." >&2
  else
    archive="$(mktemp "${TMPDIR:-/tmp}/woodbox-flutter-cache.XXXXXX")"
    trap 'rm -f "$archive"' EXIT
    python3 "$project_dir/tool/download_flutter_cache.py" "$manifest" "$archive" \
      "$sdk_dir/bin/cache/downloads/woodbox"
    tar -xzf "$archive" -C "$sdk_dir"
    rm -f "$archive"
    trap - EXIT
  fi
fi
python3 - "$sdk_dir" "$sdk_ref" "$sdk_version" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
cache = root / 'bin/cache/flutter.version.json'
if cache.exists():
    version = json.loads(cache.read_text())
    if version.get('frameworkRevision') != sys.argv[2] or version.get('frameworkVersion') != sys.argv[3]:
        cache.unlink()
        (root / 'version').unlink(missing_ok=True)
PY
printf '%s\n' "$sdk_dir"
