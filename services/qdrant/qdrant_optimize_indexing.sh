#!/usr/bin/env bash
# Mevcut Qdrant koleksiyonlarında HNSW indeksini erken aç (varsayılan eşik 20000).
set -euo pipefail

QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
THRESHOLD="${INDEXING_THRESHOLD:-1000}"

if [[ $# -gt 0 ]]; then
  collections=("$@")
else
  collections=(huv_sut_unified_catalog sut_knowledge patient_findings)
fi

for collection in "${collections[@]}"; do
  echo "→ $collection (indexing_threshold=$THRESHOLD)"
  curl -sf -X PATCH "${QDRANT_URL}/collections/${collection}" \
    -H "Content-Type: application/json" \
    -d "{\"optimizers_config\":{\"indexing_threshold\":${THRESHOLD}}}" \
    | python3 -m json.tool 2>/dev/null || echo "  (güncellendi veya koleksiyon yok)"
done

echo "Tamam. İndeks oluşması birkaç dakika sürebilir."
