#!/usr/bin/env bash
# Print chrony evidence and optionally append a public summary row to Markdown.
set -Eeuo pipefail

command -v chronyc >/dev/null || { echo "chronyc is required (install chrony)." >&2; exit 1; }
HOST="$(hostname -f 2>/dev/null || hostname)"
UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TRACKING="$(chronyc tracking)"
SOURCES="$(chronyc sources -v)"

printf '# %s — %s\n\n%s\n\n%s\n' "$HOST" "$UTC" "$TRACKING" "$SOURCES"

if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [results.md]" >&2
  exit 2
fi
if [[ $# -eq 1 ]]; then
  OUTPUT="$1"
  SYSTEM_TIME="$(awk -F: '/System time/{sub(/^[[:space:]]+/, "", $2); print $2}' <<< "$TRACKING")"
  REFERENCE="$(awk -F: '/Reference ID/{sub(/^[[:space:]]+/, "", $2); print $2}' <<< "$TRACKING")"
  STRATUM="$(awk -F: '/Stratum/{sub(/^[[:space:]]+/, "", $2); print $2}' <<< "$TRACKING")"
  if [[ ! -s "$OUTPUT" ]]; then
    printf '| Host | UTC recorded | Reference ID | Stratum | System time |\n| --- | --- | --- | --- | --- |\n' > "$OUTPUT"
  fi
  printf '| `%s` | `%s` | `%s` | `%s` | %s |\n' \
    "$HOST" "$UTC" "$REFERENCE" "$STRATUM" "$SYSTEM_TIME" >> "$OUTPUT"
  echo "Summary appended to $OUTPUT"
fi
