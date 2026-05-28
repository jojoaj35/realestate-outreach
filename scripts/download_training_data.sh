#!/bin/bash
# Download good-photo training set from Joel's Drive folders.
# Requires: gdown (pip install gdown) + folders set to "Anyone with link can view"
set -e

cd "$(dirname "$0")/.."
mkdir -p training_data/good

FOLDERS=(
  "1Kk72jpdrDaTc4_IPZQQaIaO8Gk4jbE43"
  "1i3RiK1MmLNiZW-KEpPC9UcGj0NY0Zwok"
)

for id in "${FOLDERS[@]}"; do
  echo "==> Downloading folder $id"
  gdown --folder "https://drive.google.com/drive/folders/$id" \
        -O "training_data/good/$id"
done

echo "Done. Image count:"
find training_data/good -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) | wc -l
