#!/usr/bin/env bash
# Render the Mermaid diagrams in architecture.md to PNG.
# The Mermaid code in architecture.md is the single source; never edit the PNGs by hand.
#
# Requires Mermaid CLI:  npm install -g @mermaid-js/mermaid-cli
# Usage (from anywhere):  documents/render-diagrams.sh
set -Eeuo pipefail

DOC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${DOC_DIR}/architecture.md"
# Output names, in the order the diagrams appear in architecture.md.
OUTPUTS=("architecture.png" "architecture-wol.png")

command -v mmdc >/dev/null || {
  echo "mmdc not found. Install with: npm install -g @mermaid-js/mermaid-cli" >&2; exit 1;
}
command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT

# Extract every ```mermaid block into d0.mmd, d1.mmd, ...
COUNT="$(python3 - "$SOURCE" "$WORK" <<'PY'
import re
import sys
from pathlib import Path

source, work = Path(sys.argv[1]), Path(sys.argv[2])
blocks = re.findall(r"```mermaid\n(.*?)```", source.read_text(encoding="utf-8"), re.S)
for index, block in enumerate(blocks):
    (work / f"d{index}.mmd").write_text(block, encoding="utf-8")
print(len(blocks))
PY
)"

if [[ "$COUNT" != "${#OUTPUTS[@]}" ]]; then
  echo "Expected ${#OUTPUTS[@]} Mermaid diagrams in $SOURCE, found $COUNT. Update OUTPUTS." >&2
  exit 1
fi

for index in "${!OUTPUTS[@]}"; do
  target="${DOC_DIR}/${OUTPUTS[$index]}"
  mmdc -i "${WORK}/d${index}.mmd" -o "$target" --scale 2 --backgroundColor white >/dev/null
  echo "Rendered ${OUTPUTS[$index]}"
done
