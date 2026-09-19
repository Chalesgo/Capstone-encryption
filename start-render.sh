#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

: "${DATA_ROOT:=/opt/render/project/src/data}"
mkdir -p "${DATA_ROOT}/media/seals"

# A fresh deployment has no runtime media yet. Seed only the public fallback
# seal; uploaded PDFs, QR images, and LSB-generated seals stay runtime data.
if [ ! -f "${DATA_ROOT}/media/seals/default_seal.png" ] && [ -f "media/seals/default_seal.png" ]; then
  cp "media/seals/default_seal.png" "${DATA_ROOT}/media/seals/default_seal.png"
fi

python manage.py migrate --noinput
python manage.py bootstrap_account

# The attached disk limits this service to one instance. Keep one Gunicorn
# worker as well so SQLite and the daily integrity scheduler have one owner.
export SEALGUARD_RUN_INTEGRITY_SCHEDULER=1
exec python -m gunicorn capstone_system.wsgi:application \
  --bind "0.0.0.0:${PORT:-10000}" \
  --workers 1 \
  --threads 4 \
  --timeout 180 \
  --access-logfile - \
  --error-logfile -
