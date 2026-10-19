#!/bin/sh
# Print the CHANGELOG.md section for one version (the lines under "## [X.Y.Z]" up to the next "## [").
# Usage: release-notes.sh X.Y.Z [CHANGELOG.md]. Prints nothing if the version has no section.
version="$1"
file="${2:-CHANGELOG.md}"
awk -v h="## [$version]" '
  index($0, h) == 1 { p = 1; next }
  /^## \[/          { if (p) exit }
  p
' "$file"
