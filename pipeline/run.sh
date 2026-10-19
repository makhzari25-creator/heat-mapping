#!/bin/bash
# Run the full detection pipeline for Brigade Bangalore store

set -e

RESOURCES="${1:-resources}"
OUTPUT="${2:-events_output.jsonl}"
STORE="${3:-store_1}"

echo "=== Purplle Store Intelligence Pipeline ==="
echo "Resources: $RESOURCES"
echo "Store:     $STORE"
echo "Output:    $OUTPUT"
echo ""

# Generate layout if missing
if [ ! -f "$RESOURCES/store_layout.json" ]; then
    echo "[INFO] store_layout.json not found — generating..."
    python pipeline/generate_layout.py
fi

# Run detection pipeline
echo "[INFO] Starting detection pipeline..."
python pipeline/detect.py \
    --resources "$RESOURCES" \
    --store "$STORE" \
    --output "$OUTPUT"

echo ""
echo "[INFO] Pipeline complete. Events written to $OUTPUT"
echo "[INFO] Ingest into API:"
echo "  curl -X POST http://localhost:8000/events/ingest \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d @$OUTPUT"
