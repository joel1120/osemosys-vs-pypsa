#!/usr/bin/env bash
# Verify and unpack the bundled reference artefacts into ./artifacts/.
#
# 14 MB compressed, 105 MB on disk: the input model.json, both solved runs and
# all four solver logs. The 0.9 GB and 5.8 GB labelled .lp files are NOT here --
# regenerate them with `run_benchmark.py --write-lp`.
set -euo pipefail
cd "$(dirname "$0")"

if [ -d artifacts/reference ]; then
    echo "artifacts/reference already present; nothing to do."
    exit 0
fi

echo "verifying checksum..."
sha256sum --check artifacts.tar.gz.sha256

echo "unpacking..."
tar xzf artifacts.tar.gz

echo
echo "artifacts/ ready:"
du -sh artifacts
find artifacts -name manifest.json -printf '  %p\n'
