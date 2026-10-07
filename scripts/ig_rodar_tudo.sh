#!/usr/bin/env bash
# Reprocessa o que falta e, ao terminar, retoma a sincronização do Instagram.
#
# As duas etapas disputam a MESMA GPU -- o pico do worker é 10,8 GB dos 12,3 GB
# da placa, e rodar junto deu "CUDA failed with error out of memory" em
# 2026-08-10. Encadear resolve sem babá.
#
# A lista de pendentes vem de output/ig-sync-2026-08-10/ids_restam.txt, que
# sobrevive a reboot. Regerá-la com scripts/ig_pendentes.py.
#
# Migrado de minimax-video-factory/scripts/ig_rodar_tudo.sh em 2026-10-06 --
# KB_DATABASE_URL -> DATABASE_URL, minimax_mcp.ig_worker -> workers.ig_worker,
# ./.venv/bin/python -> uv run python.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a

LISTA=output/ig-sync-2026-08-10/ids_restam.txt
IDS=$(cat "$LISTA" 2>/dev/null)
if [ -z "$IDS" ]; then
    echo "nada a reprocessar ($LISTA vazio) — indo direto para a sincronização"
else
    echo "=== $(date '+%F %H:%M:%S') reprocessando $(echo "$IDS" | wc -w) documentos ==="
    # -u para a saída não ficar presa no buffer por horas.
    uv run python -u scripts/ig_reprocessar.py $IDS --aplicar
    echo "=== $(date '+%F %H:%M:%S') reprocessamento terminou (rc=$?) ==="
fi

# Integridade ANTES de soltar o worker: melhor descobrir divergência com a fila
# parada do que com ela andando.
uv run python -c "
import os
from sqlalchemy import create_engine, text
e = create_engine(os.environ['DATABASE_URL'])
with e.connect() as c:
    t = c.execute(text('SELECT count(*) FROM documents WHERE ig_pk IS NOT NULL')).scalar()
    d = c.execute(text('SELECT count(DISTINCT ig_pk) FROM documents WHERE ig_pk IS NOT NULL')).scalar()
print(f'INTEGRIDADE: {t} documentos, {d} ig_pk distintos -> {\"ok\" if t == d else \"DIVERGENTE\"}')
"

echo "=== $(date '+%F %H:%M:%S') retomando a sincronização do Instagram ==="
# systemd-run e NÃO systemctl start: a unidade é transitória (--collect) e some
# quando para, então "start" falharia com "unit not found".
systemd-run --user --unit=ig-worker --collect \
  --working-directory="$PWD" \
  --description="ig-worker: consome a fila ig.saved" \
  /bin/bash -c 'set -a; . ./.env 2>/dev/null; set +a; exec uv run python -m workers.ig_worker >> output/ig-sync-2026-08-10/worker.log 2>&1'
sleep 10
systemctl --user is-active ig-worker.service
# UM worker só. Dois consumidores na mesma GPU causaram o OOM de 2026-08-10, e o
# mais velho ainda rodava código antigo.
echo "processos ig_worker: $(pgrep -c -f '[w]orkers.ig_worker')"
echo "=== $(date '+%F %H:%M:%S') FIM ==="
