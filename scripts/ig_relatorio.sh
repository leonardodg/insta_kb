#!/usr/bin/env bash
# Coletor do relatório do sync. Só lê -- não mexe em nada.
#
# A medida que importa aqui não é "quantos posts andaram", é GB POR POST: é ela
# que diz se os 3606 cabem nos 51 GB livres de /home, e é a única coisa neste
# relatório que não dá para estimar de outro jeito.
# A raiz sai da localização do próprio script, não de um caminho fixo: este repo
# é público, e um /home/<usuário> commitado vaza identidade além de quebrar em
# qualquer outra máquina.
#
# Migrado de minimax-video-factory/scripts/ig_relatorio.sh em 2026-10-06 --
# KB_DATABASE_URL -> DATABASE_URL, minimax_mcp.ig_worker -> workers.ig_worker,
# ./.venv/bin/python -> uv run python (este projeto não garante .venv/bin/
# fora de um `uv sync` prévio).
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
set -a; . ./.env 2>/dev/null; set +a

echo "########## RELATÓRIO $(date '+%Y-%m-%d %H:%M:%S') ##########"

echo "--- daemon ---"
# pgrep casa com o próprio comando quando o padrão aparece na linha; -f com um
# padrão que não se auto-inclui evita o falso positivo que já enganou hoje.
pgrep -af "[w]orkers.ig_worker" || echo "NENHUM WORKER DE PÉ"

echo "--- fila ---"
curl -s --max-time 5 -u guest:guest localhost:15673/api/queues 2>/dev/null \
 | python3 -c "import sys,json;[print(f\"{q['name']:16} ready={q.get('messages_ready',0):5} unacked={q.get('messages_unacknowledged',0):3} consumers={q.get('consumers',0)}\") for q in json.load(sys.stdin)]" \
 || echo "rabbit inacessível"

echo "--- banco ---"
uv run python -c "
import os
from sqlalchemy import create_engine, text
e=create_engine(os.environ['DATABASE_URL'])
with e.connect() as c:
    n=c.execute(text('SELECT count(*) FROM documents WHERE ig_pk IS NOT NULL')).scalar()
    d=c.execute(text('SELECT count(DISTINCT ig_pk) FROM documents WHERE ig_pk IS NOT NULL')).scalar()
    r=c.execute(text('SELECT count(*) FROM documents WHERE raw_file_path IS NOT NULL')).scalar()
    u=c.execute(text(\"SELECT count(*) FROM documents WHERE ig_pk IS NOT NULL AND created_at > now() - interval '1 hour'\")).scalar()
    print(f'documentos com ig_pk : {n}  (distintos {d})')
    print(f'com raw_file_path    : {r}')
    print(f'criados na última h  : {u}')
" 2>&1 | grep -v Warning

echo "--- disco e GB/post (a medida que decide) ---"
MEDIA=$(du -sm downloads/ig 2>/dev/null | cut -f1)
PASTAS=$(find downloads/ig -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
echo "mídia guardada : ${MEDIA} MB em ${PASTAS} pastas de post"
if [ "${PASTAS:-0}" -gt 0 ]; then
  python3 -c "
m=$MEDIA; p=$PASTAS
por=m/p
print(f'por post       : {por:.1f} MB')
print(f'projeção 3606  : {por*3606/1024:.1f} GB')
livre=$(df -m --output=avail /home | tail -1)
print(f'livre em /home : {livre/1024:.1f} GB')
print('VEREDITO       : ' + ('CABE' if por*3606 < livre*0.8 else '*** NÃO CABE COM FOLGA -- revisar ***'))
"
fi
df -h /home | tail -1

# Histórico do espaço LIVRE, não só da mídia. Em 2026-08-10 10:46 o /home perdeu
# 2,1 GB numa hora enquanto a mídia crescia 0,21 GB -- e o relatório não sabia
# dizer se aquilo era a corrida ou outra coisa na máquina, porque só media a
# pasta que ele mesmo enchia. Um ponto isolado não distingue tendência de ruído;
# a série distingue.
HIST=output/ig-sync-2026-08-10/disco.csv
[ -f "$HIST" ] || echo "quando,livre_mb,midia_mb,pastas,docs_ig" > "$HIST"
DOCS=$(uv run python -c "
import os
from sqlalchemy import create_engine, text
e=create_engine(os.environ['DATABASE_URL'])
with e.connect() as c: print(c.execute(text('SELECT count(*) FROM documents WHERE ig_pk IS NOT NULL')).scalar())
" 2>/dev/null | tail -1)
echo "$(date +%H:%M),$(df -m --output=avail /home | tail -1 | tr -d ' '),${MEDIA:-0},${PASTAS:-0},${DOCS:-0}" >> "$HIST"
echo "--- série do disco (livre_mb) ---"
tail -8 "$HIST"
python3 - "$HIST" <<'PY'
import csv, sys
linhas = list(csv.DictReader(open(sys.argv[1])))
if len(linhas) >= 2:
    a, b = linhas[0], linhas[-1]
    dlivre = int(a["livre_mb"]) - int(b["livre_mb"])
    dmidia = int(b["midia_mb"]) - int(a["midia_mb"])
    ddocs = int(b["docs_ig"]) - int(a["docs_ig"])
    print(f"desde {a['quando']}: livre -{dlivre} MB, mídia +{dmidia} MB, docs +{ddocs}")
    fora = dlivre - dmidia
    print(f"consumo FORA da mídia: {fora} MB", end="  ")
    if ddocs > 0:
        # o que resta a processar, ao ritmo observado
        print(f"-> por documento: {fora/ddocs:.1f} MB")
    else:
        print()
PY

echo "--- GPU ---"
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader

echo "--- últimos processados ---"
python3 -c "
import json,pathlib
p=pathlib.Path('downloads/ig/state.json')
if p.exists():
    d=json.loads(p.read_text())
    print(f'total no state: {len(d)}')
    for x in d[-8:]:
        print(' ', x.get('status'), x.get('document_id'), str(x.get('title'))[:58])
else:
    print('sem state.json ainda')
"

echo "--- erros no log do worker (últimos) ---"
grep -iE "public_request|429|login|Traceback|processing failed|could not" output/ig-sync-2026-08-10/worker.log 2>/dev/null | tail -12 || echo "nenhum"

echo "--- ritmo real ---"
grep -c "reaproveitando" output/ig-sync-2026-08-10/worker.log 2>/dev/null | sed 's/^/reusos do disco: /'
echo "########## FIM ##########"
