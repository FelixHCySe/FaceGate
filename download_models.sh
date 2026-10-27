#!/usr/bin/env bash
# Lädt die beiden ONNX-Modelle aus dem OpenCV Model Zoo (Apache-2.0 bzw. MIT).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p models
BASE="https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
for f in face_detection_yunet/face_detection_yunet_2023mar.onnx \
         face_recognition_sface/face_recognition_sface_2021dec.onnx; do
  out="models/$(basename "$f")"
  if [ -s "$out" ] && [ "$(stat -c%s "$out")" -gt 100000 ]; then
    echo "✓ $out vorhanden"
    continue
  fi
  echo "→ lade $out"
  curl -fL --retry 3 -o "$out" "$BASE/$f"
done
echo "Fertig."
