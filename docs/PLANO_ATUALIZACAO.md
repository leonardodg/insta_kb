# Atualização completa insta_kb + minimax-video-factory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) ou superpowers:executing-plans para implementar este plano task-by-task. Steps usam checkbox (`- [ ]`) para tracking.

**Goal:** Atualizar todas as dependências, ferramentas e tecnologias dos dois
projetos (incluindo ComfyUI/nodes/pesos), isolar o stack do insta_kb em
Docker, atualizar os MCPs do OpenCode, validar com a matriz completa de
testes (unit/TDD/tools/API/renders reais), drenar a fila do insta_kb e
documentar tudo com diagramas.

**Architecture:** Execução sequencial em 11 tasks (Abordagem 1 — ordem por
risco), uma por fase do plano estratégico, cada uma terminando com verificação
real, commit e status no HANDOFF. GPU de 12 GB tratada como recurso único:
nenhum job de render/transcrição/Ollama simultâneo, em todas as tasks.

**Tech Stack:** Python 3.14 + uv (2 repos), FastAPI, SQLAlchemy/Postgres+
pgvector, RabbitMQ/pika, instagrapi, faster-whisper+PyAV, fastmcp (MCP),
Docker Compose, ComfyUI v0.39.1 + MiniMax H3 (PyTorch 2.8 cu128, RTX 4080
12 GB), Ollama, OpenCode MCP.

## Global Constraints

- **GPU:** 1 job GPU por vez. Render ComfyUI ↔ transcrição faster-whisper
  (ig-worker) ↔ Ollama NUNCA simultâneos. Ordem obrigatória: renders de
  validação (Task 6) → pausa → fila (Task 7). Tasks 0-5 e 8-9 não usam GPU.
- **`av==18.1.0` fixo nos dois repos. `av` 19.0.0/19.0.1 é PROIBIDO**
  (remove `metadata_errors`; faster-whisper 1.2.1 latest sem fix).
- Python >=3.14, uv como gerenciador. Conventional commits.
- **HANDOFF é o rastreador:** ao fim de CADA task, atualizar
  `docs/HANDOFF.md` do repo afetado (task, status ✅/❌/⏳, evidência).
- **Nenhum download de peso sem OK explícito do usuário** (arquivos de 21 GB).
- TDD em qualquer mudança de código nova/corrigida; code review ao fim de
  tasks com mudança de código; verification (rodar e mostrar saída) antes de
  marcar qualquer checkbox como pronto.
- `database.db*` nunca é commitado.
- Troca `insta_kb/.mcp.json` → HTTP: último passo da Task 4, com aviso
  (corta tools desta sessão); reload na Task 5; exercitar tools na Task 6.
- Imagem ComfyUI: taggear backup **antes** de qualquer rebuild.

## Contexto e pesquisa (2026-10-07 — evidência coletada)

**`av` (teste real, decode de wav em venv py3.14):** 15.1.0 ✅ · 17.1.0 ✅ ·
18.0.0 ✅ · **18.1.0 ✅ (escolhida)** · 19.0.0 ❌ · 19.0.1 ❌ (TypeError:
`metadata_errors` removido). faster-whisper chama
`av.open(..., metadata_errors="ignore")` em `audio.py:46` nos 2 projetos.
minimax NÃO declara `av` no pyproject (bug latente: lock tem 17.1.0/18.0.0
por markers); insta_kb tem `av==15.1.0` com comentário desatualizado.

**Deps atrasadas (lock → latest):**
- insta_kb: fastapi 0.141.1→0.142.2, uvicorn 0.52.4→0.54.0, pydantic
  2.13.4→2.13.5, python-dotenv 1.2.3→1.2.4, ruff 0.16.4→0.16.10, pyright
  1.1.411→1.1.414, av 15.1.0→18.1.0; pyproject: instagrapi `>=2.1.0`→`>=3.0.20`
  (lock já resolve 3.0.20), fastmcp `>=2.0.0`→`>=4.0.11` (lock já 4.0.11).
- minimax: fastmcp 3.4.6→4.0.11 (major; API `FastMCP` igual à já testada no
  insta_kb — `src/minimax_mcp/server.py:42,97`), pydantic 2.12.5→2.13.5,
  yt-dlp 2026.7.4→2026.8.19, ruff 0.16.1→0.16.10, pylint 4.0.7→4.1.2,
  python-dotenv 1.2.2→1.2.4, av → pin novo 18.1.0.
- Ferramenta: uv CLI 0.11.29→0.12.23.

**ComfyUI:** imagem `minimax-comfyui:local` = v0.30.2 (dec5d94, 2026-08-05);
latest tag = **v0.39.1**. Upgrade = `ARG COMFYUI_TAG` no
`docker/Dockerfile` + `.env` + rebuild. Venv MCP do container =
`/opt/mcp-venv` (provisionada no build do Dockerfile) — rebuild a atualiza.

**Nodes** (`~/minimax/custom_nodes`, bind-mount): 6/8 atrasados (AcademiaSD,
Easy-Use, KJNodes, MiniMax-H3-Turbo, VideoHelperSuite, rgthree). Diffs locais
do H3-Turbo = **só CRLF** (526/526 linhas idênticas) → `git checkout -- .`
seguro. Frame-Interpolation e Impact-Pack já atualizados.

**Pesos:** instalados (08–11 ago): fl2va/ref2va pruned int8_convrot (21 GB
cada), qwen3vl int8 + nvfp4, VAEs, turbo LoRAs larryvrh. `Comfy-Org/MiniMax-H3`
modificado 2026-09-29 (novidades: w6a8, fun_controlnet_union_2.0, turbo LoRA
oficial v1.0, embeddings, video_vae_int8). `minimax_h3_experiments` tem
`sol_test1` experimental — só se pedido.

**Fila:** `ig.saved` = **329 mensagens** (rabbitmqctl, 2026-10-07).

**MCPs do OpenCode:** global `~/.config/opencode/opencode.json` tem 4
instâncias minimax (`minimax-video-factory` = docker exec no container;
`minimax-video-factory-uv` = uv host stdio; `minimax-video-factory-remote` =
HTTP 127.0.0.1:8848/mcp; `minimax-knowledge-base` = uv host, aponta pro mesmo
server.py). `insta_kb/.mcp.json` = stdio (projeto); `minimax/.mcp.json` =
docker exec + HTTP 8848. insta-kb NÃO está conectado nesta sessão.

**API insta_kb** (porta 8084, FastAPI default `/openapi.json`): `/healthcheck`,
`/ig/queue-status`, `/ig/progress`, `POST /ig/worker/start|stop`,
`GET /knowledge/search`, `POST /knowledge/ask`, `GET /knowledge/documents`,
`GET /knowledge/export/search`, `POST /knowledge/export`.

**Fatos operacionais:** `uv run python -c "import workers.ig_worker, api.main"`
OK (imports resolvem); README manda worker via `uv run python -m
workers.ig_worker` e API via `uv run uvicorn api.main:app --app-dir src`;
compose atual do insta_kb = `python` (sleep infinity, ports 8084+8849→8000) +
postgres(5433) + rabbitmq(5673/15673, hostname fixo insta-kb-rabbitmq);
containers duplicados `insta_kb_devcontainer-*` Exited(128) apontam pros
mesmos bind mounts.

## Decisões aprovadas (não repetir perguntas)

1. Escopo total: A + B + C + ComfyUI/nodes/pesos + MCPs + testes + docs +
   diagramas.
2. Abordagem 1: commits → deps insta_kb → deps minimax → ComfyUI → Docker
   insta_kb → MCPs → testes → fila → docs → diagramas.
3. Commits pendentes commitados ANTES de começar (Task 0).
4. `av==18.1.0` nos dois; `fastmcp>=4.0.11` nos dois; `instagrapi>=3.0.20`.
   **Desvio registrado na execução (Task 1):** o floor `instagrapi>=3.0.20`
   quebra o lock (instagrapi 3.0.20 pinna `pydantic==2.12.5` no marker
   Android × `pydantic>=2.13.4` do projeto) → adicionado
   `[tool.uv] environments = ["sys_platform != 'android'"]` (fix sugerido
   pelo uv; projeto é servidor Linux). Também registrado: telemetry
   default-on do FastAPI 0.142 verificado inerte (`enabled()==False` sem
   `OTEL_*` — decisão: manter default); floor `fastapi[standard]>=0.142.2`;
   `uv.lock` revision 5 exige uv ≥ 0.12 nos builds Docker (Tasks 3/4).
5. ComfyUI → v0.39.1 com imagem de backup; pesos só com OK explícito.
6. GPU serializada (ver Global Constraints).
7. Skills obrigatórias: TDD, requesting-code-review, verification-before-
   completion, git worktrees/finishing-branch quando couber, graphify (Task 9).
8. Fila do insta_kb: drenar com ig-worker real (Task 7).
9. HANDOFF atualizado ao fim de cada task.
10. Cópias do plano: `~/.claude/plans/vamos-atualizar-a-lista-groovy-sun.md`
    + `docs/PLANO_ATUALIZACAO.md` em cada repo (commit na Task 0).

## Status da execução (preencher a cada task)

| Task | Descrição | Status | Evidência / HANDOFF |
|---|---|---|---|
| 0 | Commits pendentes + baseline | ✅ | branch `update/deps-2026-10` nos 2 repos; baseline 168+9; commits insta_kb `e2095eb`+`9aaa257`, minimax `44b6406`+`4b34bd2`+`376e6ae` |
| 1 | Deps insta_kb | ✅ | `av==18.1.0`+`instagrapi>=3.0.20`+`fastmcp>=4.0.11`+`fastapi[standard]>=0.142.2`; lock uvicorn 0.54.0/ruff 0.16.10/pyright 1.1.414; 168 passed + smoke decode OK; review aplicado; `58e8093` |
| 2 | Deps minimax | ✅ | `fastmcp>=4.0.11`+`av==18.1.0` (pin novo)+`requires-python>=3.11`; lock yt-dlp 2026.8.19/pydantic 2.13.5/ruff 0.16.10/pylint 4.1.2; review: sem breaking no server, fixes pós-review; 9/9+privacy+ruff ✅; commits `715216e`+`83c8454` |
| 3 | ComfyUI + nodes + pesos | ✅ | v0.39.1 + nodes já atualizados + 3 pesos novos (turbo loras fl2v/ref2v + vae int8_convrot, 6,7 GB); render turbo validado (seed 42, 512x320, 5s, vídeo+áudio estéreo OK); commit `ae944c1` |
| 4 | Docker insta_kb (Parte B) | ✅ (Ollama corrigido) | venv isolado `/opt/venv`; rede `insta-kb-net` + api/worker/mcp; Ollama CONTAINERIZADO na rede (host.docker.internal não alcançava `insta-kb-net` -- firewall; corrigido depois de causar um incidente real); `.mcp.json`→HTTP; mutex de GPU compartilhado com minimax; commits `bf824e5`+`02140f1`+`984e03c`+posteriores |
| 5 | MCPs do OpenCode | 🔶 parcial | inventário feito; `insta-kb` global trocado pra HTTP; pendente: reload do OpenCode (ação do usuário) pra Steps 4-5 |
| 6 | Matriz de testes | 🔶 parcial | Steps 1/2/4 feitos; Step 3 bloqueado (reload pendente); Step 5 feito com incidente real documentado (ver HANDOFF) |
| 7 | Fila do insta_kb | ✅ (em andamento) | 322→199 ready processados após fix do Ollama containerizado (queue real drenando sozinha, worker rodando); 0 dead observado; retomável/pausável via ig_worker_start/stop |
| 8 | Documentação | ✅ | site MkDocs nos 2 repos (mkdocs.yml + mkdocstrings); README/HANDOFF atualizados; commits `53a92ae` (insta_kb) + `fe4fc82` (minimax) |
| 9 | Diagramas | ✅ | docs/ARQUITETURA.md com 3 diagramas Mermaid nos 2 repos; feito junto com a Task 8 pela mesma sessão paralela |
| 10 | Auditoria SOLID (avaliação) | ✅ | relatórios `docs/SOLID_AUDIT.md` ×2 (314 l. insta_kb / 231 l. minimax, só avaliação); veredito **ciclo futura** nos 2; 1 bug-crítico `state=rendering` (minimax) + backlog 12 itens (insta_kb); commits `02f760a`+`76c60a6` |

---

### Task 0: Commits pendentes + baseline + cópias do plano

**Files:**
- Modify (commit): `insta_kb: src/core/knowledge/knowledge.py`,
  `tests/test_core_knowledge.py`
- Modify (commit): `minimax: .env.example`, `docker/docker-compose.yml`,
  `scripts/config.sh` | `docs/MCP_TOOLS.md`, `scripts/generate_mcp_docs.py`
- Create: `insta_kb/docs/PLANO_ATUALIZACAO.md`,
  `minimax/docs/PLANO_ATUALIZACAO.md` (cópias do plano)

**Interfaces:**
- Produces: `main` limpo nos 2 repos (exceto `database.db*` untracked);
  baseline de testes registrado; planos versionados.

- [ ] **Step 1: Mostrar diffs e obter aprovação**

```bash
git -C $HOME/localhost/insta_kb diff --stat
git -C $HOME/localhost/insta_kb diff
git -C $HOME/tools-local/minimax-video-factory diff --stat
git -C $HOME/tools-local/minimax-video-factory diff
```
Expected: 2 arquivos no insta_kb (fail-soft + resolved path + 48 linhas de
teste) e 5 no minimax; usuário confirma a divisão dos commits.

- [ ] **Step 2: Baseline verde nos dois repos**

```bash
cd $HOME/localhost/insta_kb && uv run pytest -q
cd $HOME/tools-local/minimax-video-factory && uv run pytest -q
```
Expected: insta_kb `168 passed` (ou mais); minimax todos os `unit_*` PASS.
Registrar contagens exatas — é o baseline de regressão das próximas tasks.

- [ ] **Step 3: Commit insta_kb (fail-soft + export resolved path)**

Nota: a divisão em 2 commits originalmente proposta foi descartada — os hunks
intercalam no mesmo arquivo (`knowledge.py`); commit único coerente.

```bash
cd $HOME/localhost/insta_kb
git add src/core/knowledge/knowledge.py tests/test_core_knowledge.py
git commit -m "fix(knowledge): fail-soft nos tools MCP e export usa resolved path (+testes)"
```
Expected: commit criado; hooks do pre-commit (ruff/pyright/bandit/pip-audit)
passam.

- [ ] **Step 4: Commits do minimax (2)**

```bash
cd $HOME/tools-local/minimax-video-factory
git add .env.example docker/docker-compose.yml scripts/config.sh
git commit -m "build: default INT8 ConvRot + nvfp4 AWQ (compose, .env.example, config.sh)"
git add docs/MCP_TOOLS.md scripts/generate_mcp_docs.py
git commit -m "docs: regenera MCP_TOOLS.md"
```
Expected: 2 commits; `git status` só com o que sobrar.

- [ ] **Step 5: Versionar as cópias do plano**

```bash
cp ~/.claude/plans/vamos-atualizar-a-lista-groovy-sun.md $HOME/localhost/insta_kb/docs/PLANO_ATUALIZACAO.md
cp ~/.claude/plans/vamos-atualizar-a-lista-groovy-sun.md $HOME/tools-local/minimax-video-factory/docs/PLANO_ATUALIZACAO.md
git -C $HOME/localhost/insta_kb add docs/PLANO_ATUALIZACAO.md
git -C $HOME/localhost/insta_kb commit -m "docs: plano de atualização 2026-10-07"
git -C $HOME/tools-local/minimax-video-factory add docs/PLANO_ATUALIZACAO.md
git -C $HOME/tools-local/minimax-video-factory commit -m "docs: plano de atualização 2026-10-07"
```
Expected: `database.db*` continua untracked e fora dos commits.

- [ ] **Step 6: HANDOFF Task 0 + tabela de status**

Atualizar `insta_kb/docs/HANDOFF.md` e
`minimax/docs/HANDOFF.md` (seção "EM ANDAMENTO 2026-10-07"): Task 0 ✅ com
contagens do baseline; marcar linha 0 da tabela de status deste plano como ✅
(sincronizar as 3 cópias do plano depois de qualquer edição da tabela).
Commit docs.

---

### Task 1: Deps insta_kb (Parte A)

**Files:**
- Modify: `insta_kb/pyproject.toml` (bloco `av`, `instagrapi`, `fastmcp`)
- Modify: `insta_kb/uv.lock`

**Interfaces:**
- Produces: `av==18.1.0`, `instagrapi>=3.0.20`, `fastmcp>=4.0.11`, lock com
  fastapi 0.142.2+/uvicorn 0.54+/ruff 0.16.10/pyright 1.1.414.

- [ ] **Step 1: Atualizar CLI uv**

```bash
uv self update && uv --version
```
Expected: `uv 0.12.23`. Fallback se `self update` não existir:
`curl -LsSf https://astral.sh/uv/install.sh | sh` e revalidar `uv --version`.

- [ ] **Step 2: Editar pyproject.toml — pin do av (substituir o bloco atual)**

Trocar o comentário + `"av==15.1.0"` por:

```toml
    # Pinned, not >=: faster-whisper 1.2.1's decode_audio() calls
    # av.open(..., metadata_errors="ignore") -- a kwarg av removed in 19.0.0.
    # Verified 2026-10-07 on Python 3.14 with a real decode_audio():
    # 15.1.0/17.1.0/18.0.0/18.1.0 pass; 19.0.0/19.0.1 raise TypeError.
    # faster-whisper latest (1.2.1) still declares av>=11 with no upper
    # bound, so the pin is what keeps transcription working. 18.1.0 = newest
    # release that works. See docs/HANDOFF.md (2026-10-07).
    "av==18.1.0",
```

Também: `"instagrapi>=2.1.0"` → `"instagrapi>=3.0.20"` e
`"fastmcp>=2.0.0"` → `"fastmcp>=4.0.11"`.

- [ ] **Step 3: Re-resolver o lock**

```bash
cd $HOME/localhost/insta_kb && uv lock --upgrade
grep -A1 '^name = "av"$' uv.lock | head -3
grep -A1 '^name = "fastapi"$' uv.lock | head -2
grep -A1 '^name = "uvicorn"$' uv.lock | head -2
grep -A1 '^name = "ruff"$' uv.lock | head -2
```
Expected: av `18.1.0`; fastapi `>=0.142.2`; uvicorn `>=0.54.0`; ruff
`0.16.10`. Se `uv lock --upgrade` puxar `av 19.x`, FALHOU o pin — investigar
antes de seguir.

- [ ] **Step 4: Suíte de validação completa**

```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run bandit -c pyproject.toml -r src
uv run pip-audit
uv run pytest -q
```
Expected: 0 erros em todos; `168 passed` (baseline da Task 0) — qualquer
queda de teste é regressão, não seguir.

- [ ] **Step 5: Smoke de transcrição com av 18.1.0 (TDD do ambiente)**

```bash
cd $HOME/localhost/insta_kb && uv run python - <<'EOF'
import wave, struct, math, av
from faster_whisper.audio import decode_audio
p = "/tmp/av18_smoke.wav"
with wave.open(p, "w") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    for i in range(16000):
        w.writeframes(struct.pack("<h", int(12000*math.sin(2*math.pi*440*i/16000))))
print("av", av.__version__)
d = decode_audio(p)
print("decode_audio OK, samples:", len(d))
EOF
```
Expected: `av 18.1.0` + `decode_audio OK, samples: 16000` (sem TypeError).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): av==18.1.0 (novo commentário), instagrapi>=3.0.20, fastmcp>=4.0.11 + lock --upgrade"
```

- [ ] **Step 7: Code review + HANDOFF**

`requesting-code-review` no diff (pyproject/uv.lock). HANDOFF:
Task 1 ✅ + versões finais + resultado do smoke de transcrição. Marcar linha 1
na tabela das 3 cópias do plano.

---

### Task 2: Deps minimax (Parte C)

**Files:**
- Modify: `minimax/pyproject.toml` (novo pin `av`, bump `fastmcp`)
- Modify: `minimax/uv.lock`

**Interfaces:**
- Produces: `fastmcp>=4.0.11` + lock 4.0.11, `av==18.1.0` no pyproject/lock;
  container MCP venv (`/opt/mcp-venv`) atualizado no rebuild da Task 3
  (dependência declarada — verificar na Task 3).

- [ ] **Step 1: Ler o changelog do fastmcp 3→4**

```bash
# via webfetch: https://github.com/jlowin/fastmcp/releases (ou PyPI 4.0.11)
```
Expected: lista escrita de breaking changes que afetem
`src/minimax_mcp/server.py` (uso de `FastMCP(...)`, `@mcp.tool`, `mcp.run`).
Se houver breaking change no que o server usa, anotar a correção e tratá-la
com **TDD** no Step 5.

- [ ] **Step 2: Editar pyproject.toml**

Adicionar ao array de `dependencies` (com o comentário):

```toml
    # Same trap as insta_kb: faster-whisper 1.2.1 needs av<19 (metadata_errors
    # removed in 19.0.0). Verified 2026-10-07 -- see docs/HANDOFF.md.
    "av==18.1.0",
```

Trocar `"fastmcp>=3.0.0"` → `"fastmcp>=4.0.11"`.

- [ ] **Step 3: Re-resolver + conferir versões**

```bash
cd $HOME/tools-local/minimax-video-factory && uv lock --upgrade
grep -A1 '^name = "fastmcp"$' uv.lock | head -2   # 4.0.11
grep -A1 '^name = "av"$' uv.lock | head -4        # 18.1.0 (não 19.x)
grep -A1 '^name = "yt-dlp"$' uv.lock | head -2    # 2026.8.19+
grep -A1 '^name = "pydantic"$' uv.lock | head -4  # 2.13.5
```
Expected: valores batendo. av 19.x = parar e investigar.

- [ ] **Step 4: Validação (ruff + testes unit)**

```bash
uv run ruff check .
uv run pytest -q
```
Expected: 0 erros; mesmo baseline da Task 0.

- [ ] **Step 5: TDD se o changelog exigir mudança de código**

Se (e só se) o Step 1 apontou breaking change no uso do server:
1. Escrever o teste falhando em `tests/` (padrão dos `unit_*.py`).
2. Rodar → FAIL.
3. Corrigir `src/minimax_mcp/server.py`.
4. Rodar → PASS.
5. Commit da correção separado: `fix(mcp): adapta server ao fastmcp 4`.
Se nada exigir mudança: registrar "sem breaking changes no uso atual" no
HANDOFF e seguir.

- [ ] **Step 6: Smoke do MCP via stdio host**

```bash
cd $HOME/tools-local/minimax-video-factory
timeout 15 uv run python -c "
import os; os.environ['MCP_TRANSPORT']='stdio'
from minimax_mcp.server import mcp
print('FastMCP importado:', mcp.name)
"
```
Expected: importa sem erro com fastmcp 4.0.11. (As instâncias conectadas ao
OpenCode só trocam de venv no reload — Task 5. A instância in-container só
após o rebuild da Task 3 — registrar essa dependência.)

- [ ] **Step 7: Code review + commit + HANDOFF**

`requesting-code-review` do diff; commit
`chore(deps): fastmcp>=4.0.11, av==18.1.0 (pin novo) + lock --upgrade`;
HANDOFF Task 2 ✅; linha 2 da tabela nas 3 cópias.

---

### Task 3: ComfyUI v0.39.1 + nodes + pesos

**Files:**
- Modify: `minimax/docker/Dockerfile` (`ARG COMFYUI_TAG`),
  `minimax/.env` (`COMFYUI_TAG`), `.env.example` se necessário
- Modify (git pull): 6 nodes em `~/minimax/custom_nodes/`
- Add (se aprovados): pesos novos em `~/minimax/models/` (bind-mount dos
  containers)
- GPU: sim — render de validação (nenhum outro job GPU nesta janela)

**Interfaces:**
- Produces: imagem `minimax-comfyui:local` = v0.39.1 (backup
  `minimax-comfyui:v0.30.2-backup`), nodes atualizados, venv MCP
  `/opt/mcp-venv` com fastmcp 4.0.11/av 18.1.0, baseline de render novo.

- [ ] **Step 1: Backup da imagem atual**

```bash
docker tag minimax-comfyui:local minimax-comfyui:v0.30.2-backup
docker images | grep minimax-comfyui
```
Expected: 2 tags visíveis (`v0.30.2-backup` + `local`).

- [ ] **Step 2: Atualizar os 6 nodes atrasados**

```bash
for d in comfyui_AcademiaSD ComfyUI-Easy-Use ComfyUI-KJNodes \
         ComfyUI-MiniMax-H3-Turbo ComfyUI-VideoHelperSuite rgthree-comfy; do
  echo "== $d"; git -C ~/minimax/custom_nodes/$d checkout -- . 2>/dev/null
  git -C ~/minimax/custom_nodes/$d pull --ff-only || echo "PULL FALHOU: $d"
done
for d in ~/minimax/custom_nodes/*/; do
  [ -d "$d/.git" ] && echo "$(basename $d): $(git -C $d log -1 --format=%h)"
done
```
Expected: pulls OK (ff-only); se algum falhar (divergência real != CRLF),
PARAR e mostrar o diff local antes de decidir (só CRLF conhecido = reset;
conteúdo real = perguntar). Anotar se algum node pede dependência nova
(requirements) — entra no `docker/Dockerfile` antes do build.

- [ ] **Step 3: Bump da tag do ComfyUI**

Em `docker/Dockerfile`: `ARG COMFYUI_TAG=v0.30.2` → `ARG COMFYUI_TAG=v0.39.1`.
Em `.env`: `COMFYUI_TAG=v0.30.2` → `COMFYUI_TAG=v0.39.1`. Conferir se o
compose/Dockerfile usa o ARG do `.env` (`${COMFYUI_TAG:-v0.30.2}`) e alinhar
as duas fontes.

- [ ] **Step 4: Rebuild**

```bash
cd $HOME/tools-local/minimax-video-factory
docker compose -f docker/docker-compose.yml build comfyui 2>&1 | tail -30
```
Expected: build OK; log mostra checkout de `v0.39.1` (o passo
`git rev-parse --short HEAD` do Dockerfile). Se node pedir dependência nova e
o pip install do build falhar, corrigir Dockerfile e rebuild.

- [ ] **Step 5: Venv MCP da imagem atualizada**

```bash
docker exec minimax-comfyui /opt/mcp-venv/bin/python -c \
  "import fastmcp, av; print('fastmcp', fastmcp.__version__, '| av', av.__version__)"
```
Expected: `fastmcp 4.0.11 | av 18.1.0`. Se não bater: o Dockerfile instala
deps do projeto no build (`UV_PROJECT_ENVIRONMENT=/opt/mcp-venv`) — conferir
se ele faz `uv sync` do lock atualizado e rebuildar com `--no-cache`.
ATENÇÃO (review Task 2): `docker/Dockerfile` só copia `pyproject.toml`
(não `uv.lock`) e o `uv sync` não usa `--frozen` — a imagem re-resolve dos
ranges (`av==18.1.0` protege, `fastmcp>=4.0.11` flutua). Decidir aqui:
`COPY uv.lock` + `uv sync --frozen` (reprodutível) ou registrar float
intencional no HANDOFF.

- [ ] **Step 6: Checkpoint de pesos (pedir OK — sem download antes)**

```bash
cd $HOME/tools-local/minimax-video-factory && uv run python - <<'EOF'
import json, urllib.request, hashlib, os
repo = "Comfy-Org/MiniMax-H3"
api = json.load(urllib.request.urlopen(f"https://huggingface.co/api/models/{repo}"))
files = {s["rfilename"]: s for s in api["siblings"]}
roots = {"/comfy/ComfyUI/models": os.path.expanduser("~/minimax/models")}
for path, localroot in roots.items():
    for name, meta in sorted(files.items()):
        local = os.path.join(localroot, name)
        tag = "LOCAL?" if os.path.exists(local) else "NEW"
        print(f"{tag:6} {name}")
EOF
```
Expected: lista `NEW` (candidatas: `diffusion_models/*w6a8*`,
`model_patches/*controlnet_union_2.0*`, `loras/minimax_h3_fl2v_turbo_4step_v1.0*`,
`vae/minimax_h3_video_vae_int8_convrot.safetensors`, `embeddings/*`). Mostrar
lista + tamanhos ao usuário e **obter OK por arquivo** (question tool). Para
arquivos que já existem localmente, comparar sha256 local vs etag HF antes de
decidir re-download (script no momento do checkpoint). Baixar SÓ o aprovado:

```bash
# exemplo para arquivo aprovado:
huggingface-cli download Comfy-Org/MiniMax-H3 <arquivo> \
  --local-dir $HOME/minimax/models
```

- [ ] **Step 7: Subir stack + health check**

```bash
cd $HOME/tools-local/minimax-video-factory
docker compose -f docker/docker-compose.yml up -d comfyui
sleep 20 && curl -s http://127.0.0.1:8188/system_stats | head -c 400
```
E, via MCP (instância `minimax-video-factory`): `health_check` →
`{"ok": true, ...}` com os 4 arquivos de modelo presentes e ComfyUI ≥ 0.39.1.

- [ ] **Step 8: Render turbo de validação (GPU exclusiva)**

Antes: `docker exec minimax-comfyui nvidia-smi` → sem processos de inferência
(ig-worker/Ollama parados nesta janela).

Via MCP `generate_video` (ou `submit_scene`): turbo, 5 s, 512×320, seed fixo
(p.ex. 42). Medir: tempo de parede, VRAM pico (`/system_stats` depois),
sucesso do arquivo em `output/`. Depois: `docker stop minimax-comfyui;
docker start minimax-comfyui` NÃO — rollback real só na Step 10.

- [ ] **Step 9: Comparar com baseline**

Escolher 1-2 outputs antigos de `output/` (pré-atualização) e o novo clip;
registrar no HANDOFF: tempo, VRAM pico, observação visual (movimento,
sincronia de áudio, artefatos). Incluir no HANDOFF qualquer mudança de
comportamento do sampler (ModelSamplingAV dual-schedule agora nativo).

- [ ] **Step 10: Rollback se falhou + commit + HANDOFF**

Se render/health falharem: `COMFY_IMAGE=minimax-comfyui:v0.30.2-backup` no
`.env` + `docker compose up -d comfyui` (sem rebuild) e registrar no HANDOFF.
Commit das mudanças de config (Dockerfile/.env.example) +
`requesting-code-review`; HANDOFF Task 3 ✅ com números; linha 3 da tabela.

---

### Task 4: Docker insta_kb — Parte B (isolar stack)

**Files:**
- Modify: `insta_kb/.devcontainer/docker-compose.yml` (network, ollama, api,
  worker, mcp; ports do `python` removidas)
- Modify: `insta_kb/.devcontainer/Dockerfile` (ffmpeg + libs NVIDIA)
- Modify: `insta_kb/.mcp.json` (**último step** — HTTP)
- GPU: não nesta task (só configs/build); ollama com passthrough configurado
  mas **sem carregar modelo** (nada de GPU concorrente)

**Interfaces:**
- Consumes: stack base postgres/rabbitmq já up.
- Produces: serviços `api` (127.0.0.1:8084→8000), `worker` (GPU, sem porta),
  `mcp` (127.0.0.1:8849, streamable-http), `ollama` (rede interna), rede
  `insta-kb-net`; `.mcp.json` HTTP.

- [ ] **Step 1: Limpar duplicata de containers**

```bash
docker compose -p insta_kb_devcontainer down
docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -E "devcontainer|insta"
```
Expected: conjunto `insta_kb_devcontainer-*` removido (estava Exited(128));
`devcontainer-{postgres,rabbitmq}-1` permanece Up (é o real — mesmos binds).

- [ ] **Step 2: Verificar como o serviço `python` resolve o interpretador**

```bash
docker compose -f .devcontainer/docker-compose.yml run --rm --no-deps python \
  sh -c 'which python; python -V; which uv'
```
Expected: python/uv funcionando. Contexto: o bind `../:/app` cobre o
`/app/.venv` da imagem com o `.venv` do host (symlink para
`$HOME/.cache/uv/...`, invisível dentro do container). Se o
interpretador estiver quebrado, os serviços novos não podem confiar no
`VIRTUAL_ENV` da imagem — usar `uv run` (que sincroniza) como comando e
registrar a descoberta. Este passo decide o formato dos comandos dos
próximos steps.

- [ ] **Step 3: Dockerfile — ffmpeg + libs NVIDIA**

Em `.devcontainer/Dockerfile`, bloco `apt-get install`: adicionar `ffmpeg`.
Apos o `uv sync`, adicionar (se o Step 2 usar uv run com env da imagem):

```dockerfile
RUN pip install --no-cache-dir nvidia-cublas-cu12 nvidia-cudnn-cu12
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.14/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.14/site-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH}
```
(Ajustar o site-packages real após `python -V` — verificar com
`pip show nvidia-cublas-cu12` no build. Se o wheel do ctranslate2 for só CPU,
registrar no HANDOFF que transcrição GPU in-container exige imagem base CUDA
— decisão registrada, fallback = worker no host como hoje.)

- [ ] **Step 4: Compose — rede + serviços novos**

Adicionar ao topo e transformar serviços (blocos exatos):

```yaml
networks:
  insta-kb-net:
    name: insta-kb-net

services:
  # (python existente: REMOVER as duas ports; manter só como shell de debug,
  #  command: sleep infinity, com networks: [insta-kb-net])

  ollama:
    image: ollama/ollama
    restart: unless-stopped
    networks: [insta-kb-net]
    environment:
      OLLAMA_MAX_LOADED_MODELS: "1"
    volumes:
      - ${OLLAMA_MODELS_DIR:?defina OLLAMA_MODELS_DIR no .env}:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    # sem ports — só rede interna

  api:
    build: { context: ../, dockerfile: ./.devcontainer/Dockerfile }
    user: appuser
    networks: [insta-kb-net]
    env_file: [../.env]
    environment:
      POSTGRES_HOST: postgres
      RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/
      OLLAMA_URL: http://ollama:11434
      MCP_TRANSPORT: streamable-http
    command: uv run uvicorn api.main:app --app-dir src --host 0.0.0.0 --port 8000
    ports: ["127.0.0.1:${API_PORT:-8084}:8000"]
    depends_on: [postgres, rabbitmq, ollama]
    volumes: [../:/app]

  worker:
    build: { context: ../, dockerfile: ./.devcontainer/Dockerfile }
    user: appuser
    networks: [insta-kb-net]
    env_file: [../.env]
    environment:
      POSTGRES_HOST: postgres
      RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/
      OLLAMA_URL: http://ollama:11434
    command: uv run python -m workers.ig_worker
    volumes: [../:/app]
    depends_on: [postgres, rabbitmq, ollama]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  mcp:
    build: { context: ../, dockerfile: ./.devcontainer/Dockerfile }
    user: appuser
    networks: [insta-kb-net]
    env_file: [../.env]
    environment:
      POSTGRES_HOST: postgres
      RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/
      OLLAMA_URL: http://ollama:11434
      MCP_TRANSPORT: streamable-http
      MCP_PORT: "8849"
    command: uv run python src/mcp_server/server.py
    ports: ["127.0.0.1:${MCP_PORT:-8849}:8849"]
    depends_on: [postgres, rabbitmq, ollama]
    volumes: [../:/app]
```

postgres/rabbitmq: adicionar `networks: [insta-kb-net]` aos dois. Conferir
Valor real de `OLLAMA_MODELS_DIR` (definido no `.env` do host; `ls` do path
+ `/.ollama` deve existir — ajustar se for outro). Confirmar porta interna: server usa
`settings.MCP_PORT` (8849) — mapear 8849→8849 (corrigir do 8000 antigo).

- [ ] **Step 5: Build + subir infra**

```bash
docker compose -f .devcontainer/docker-compose.yml build
docker compose -f .devcontainer/docker-compose.yml up -d postgres rabbitmq ollama
sleep 15
docker compose -f .devcontainer/docker-compose.yml ps
```
Expected: postgres/rabbitmq/ollama Up; postgres saudável
(`docker exec devcontainer-postgres-1 pg_isready -U kb` → accepting).

- [ ] **Step 6: Subir api + worker + mcp e checar logs**

```bash
docker compose -f .devcontainer/docker-compose.yml up -d api worker mcp
sleep 20
docker compose -f .devcontainer/docker-compose.yml logs api mcp worker 2>&1 | grep -iE "127\.0\.0\.1|localhost.*(refused|failed)" || echo "SEM ERROS DE HOST"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8084/healthcheck
curl -s http://127.0.0.1:8084/ig/queue-status | head -c 300
```
Expected: nenhum erro `refused` de hostname host-based; `/healthcheck` →
`200`; queue-status JSON. Logs do worker devem mostrar conexão via nomes de
serviço (`rabbitmq`, `postgres`).

- [ ] **Step 7: Smoke da API + mcp transport**

```bash
curl -s http://127.0.0.1:8084/openapi.json | python3 -c "import json,sys; print(len(json.load(sys.stdin)['paths']), 'endpoints')"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8849/mcp   # deve NÃO ser 000
```
Expected: 10 endpoints; porta 8849 respondendo (HTTP MCP vivo).

- [ ] **Step 8: Trocar insta_kb/.mcp.json para HTTP (COM AVISO)**

**AVISAR o usuário antes:** esta troca corta as tools `insta-kb` desta sessão
até o reload (Task 5). Conteúdo novo de `insta_kb/.mcp.json`:

```json
{
  "mcpServers": {
    "insta-kb": {
      "type": "http",
      "url": "http://127.0.0.1:8849/mcp"
    }
  }
}
```

- [ ] **Step 9: Code review + commit + HANDOFF**

Commit: `feat(docker): isola api/worker/mcp/ollama em containers + mcp via
streamable-http`. `requesting-code-review`. HANDOFF Task 4 ✅ (incluindo a
descoberta do Step 2 e o estado da GPU libs); linha 4 da tabela.

---

### Task 5: Atualizar os MCPs existentes do OpenCode

**Files:**
- Read: `~/.config/opencode/opencode.json` (4 instâncias minimax),
  `insta_kb/.mcp.json`, `minimax/.mcp.json`
- Reload: sessão OpenCode (ação do usuário)

**Interfaces:**
- Consumes: lock novo (Task 1/2), imagem rebuildada (Task 3), `.mcp.json`
  HTTP (Task 4).
- Produces: todas as instâncias MCP conectadas e com contagem de tools
  registrada (base do checklist da Task 6).

- [ ] **Step 1: Inventário das instâncias**

```bash
grep -o '"minimax[^"]*"\|"insta-kb"' ~/.config/opencode/opencode.json \
  $HOME/localhost/insta_kb/.mcp.json \
  $HOME/tools-local/minimax-video-factory/.mcp.json | sort -u
```
Expected: tabela (nome, tipo, comando/url). Confirmar quais estão `enabled`
e mapear: 4 minimax (global) + insta-kb (HTTP novo) + 2 do minimax/.mcp.json.

- [ ] **Step 2: Verificar venvs dos caminhos host/container**

```bash
# host (uv usa o lock novo automaticamente no próximo start):
cd $HOME/tools-local/minimax-video-factory && uv run python -c "import fastmcp; print(fastmcp.__version__)"   # 4.0.11
# container (Task 3 Step 5 já validou — repetir como confirmação):
docker exec minimax-comfyui /opt/mcp-venv/bin/python -c "import fastmcp; print(fastmcp.__version__)"               # 4.0.11
```
Expected: ambos 4.0.11.

- [ ] **Step 3: Reload da sessão OpenCode (ação do usuário)**

Instruir: reiniciar a sessão OpenCode (sair e reabrir no projeto) para que
todos os MCPs reconectem com o código/venvs novos e o `.mcp.json` HTTP seja
lido. **Aviso:** as tools desta sessão caem e voltam.

- [ ] **Step 4: Handshake + contagem de tools**

Após reload: listar tools por servidor (prefixos
`mcp__minimax-video-factory__*`, `...-uv__*`, `...-remote__*`,
`mcp__minimax-knowledge-base__*`, `mcp__insta-kb__*`). Registrar contagem
esperada por instância (minimax: 12 tools × 4 instâncias; insta-kb: N tools).
Expected: nenhuma instância ausente; anotar N do insta-kb.

- [ ] **Step 5: HANDOFF**

HANDOFF Task 5 ✅ + tabela de instâncias/contagens; linha 5 da tabela nas 3
cópias do plano.

---

### Task 6: Matriz completa de testes (GPU serializada aqui)

**Files:**
- Nenhum código (testes executados); registro em `HANDOFF.md` (checklist
  PASS/FAIL) e no plano.
- GPU: sim — renders; **worker/ollama devem estar PARADOS** (Task 7 vem
  depois).

**Interfaces:**
- Consumes: MCPs da Task 5, API da Task 4, ComfyUI da Task 3.
- Produces: checklist completo com evidência; 2-3 clips novos vs baseline.

- [ ] **Step 1: Unit + TDD check**

```bash
cd $HOME/localhost/insta_kb && uv run pytest -q
cd $HOME/tools-local/minimax-video-factory && uv run pytest -q
uv run ruff check . && uv run pyright   # insta_kb
```
Expected: mesmo baseline Task 0 (168+/verde).

- [ ] **Step 2: Tools CPU do minimax, uma a uma (via MCP desta sessão)**

Chamar e registrar PASS/FAIL + evidência (1 linha por tool):
`health_check`, `queue_status`, `list_outputs`, `get_status` (id inexistente →
erro controlado), `download_video` (URL curta conhecida),
`create_cinematic_prompt` (transcrição de exemplo curta).
Expected: cada tool responde `{"ok": true}` ou erro controlado documentado.

- [ ] **Step 3: Tools insta-kb, uma a uma (via MCP pós-reload)**

Para cada tool listado no handshake da Task 5: chamada mínima + PASS/FAIL.
Expected: cobertura 100% da lista; qualquer tool morta = fix com TDD ou
registrar no HANDOFF.

- [ ] **Step 4: APIs REST + ComfyUI (curl)**

```bash
B=http://127.0.0.1:8084
curl -s -o /dev/null -w 'healthcheck %{http_code}\n' $B/healthcheck                 # 200
curl -s -o /dev/null -w 'queue-status %{http_code}\n' $B/ig/queue-status             # 200
curl -s -o /dev/null -w 'progress %{http_code}\n' $B/ig/progress                     # 200
curl -s -o /dev/null -w 'search %{http_code}\n' "$B/knowledge/search?q=teste&top_k=3" # 200
curl -s -o /dev/null -w 'documents %{http_code}\n' $B/knowledge/documents            # 200
curl -s -o /dev/null -w 'export-search %{http_code}\n' "$B/knowledge/export/search?q=teste" # 200
# resolved path fix (traversal deve FALHAR com ok:false):
curl -s -X POST $B/knowledge/export -H 'content-type: application/json' \
  -d '{"ids":[1],"output_dir":"../../etc"}' | head -c 300                            # ok:false
C=http://127.0.0.1:8188
curl -s -o /dev/null -w 'comfy stats %{http_code}\n' $C/system_stats                 # 200
curl -s -o /dev/null -w 'comfy objects %{http_code}\n' $C/object_info                # 200
curl -s -X POST $C/prompt -H 'content-type: application/json' \
  -d @workflows/minimax_h3_t2v_turbo_api.json | head -c 200                          # prompt_id
```
Expected: códigos como anotado; export com traversal retorna `ok:false`
(validação de resolved path); `/prompt` aceita o workflow.

- [ ] **Step 5: Renders reais novos vs baseline (GPU exclusiva)**

Pré-check: worker parado (`docker compose ... ps worker` → not running) e
`nvidia-smi` sem processos de inferência.
Renders (seeds fixos, registrados):
1. turbo 4-step, 5 s, 512×320, seed 42 (via `generate_video`/`submit_scene`).
2. t2v padrão, 5 s, 1024×576, seed 42.
3. r2v com `first_frame` de um output antigo.
Para cada um: tempo de parede, VRAM pico (`/system_stats` antes/depois),
arquivo em `output/`, avaliação visual vs 1-2 outputs pré-atualização.
Expected: 3 clips gerados; tabela tempo/VRAM/observação no HANDOFF.

- [ ] **Step 6: Code review geral + HANDOFF**

`requesting-code-review` do estado final dos 2 repos. HANDOFF Task 6 ✅ com
checklists completos (tools × instâncias, endpoints, renders); linha 6.

---

### Task 7: Fila pendente do insta_kb (GPU — após pausa)

**Files:**
- Nenhum código; operação do `worker` em container.
- GPU: sim — transcrição faster-whisper. **ComfyUI DEVE estar ocioso.**

**Interfaces:**
- Consumes: stack Docker (Task 4), API (Task 4).
- Produces: fila `ig.saved` drenada (ou parada proposital registrada), posts
  na KB validados.

- [ ] **Step 1: Pausa e pré-check de GPU**

```bash
docker exec minimax-comfyui nvidia-smi | tail -5   # sem processos de inferência
docker exec devcontainer-rabbitmq-1 rabbitmqctl list_queues name messages
```
Expected: `ig.saved` ≈ 329; GPU livre. Se não livre, aguardar (regra global).

- [ ] **Step 2: Subir o worker**

```bash
cd $HOME/localhost/insta_kb
docker compose -f .devcontainer/docker-compose.yml up -d worker
docker compose -f .devcontainer/docker-compose.yml logs -f worker | head -40
```
Expected: conecta em `rabbitmq`/`postgres` por nome de serviço, baixa
mensagens de `ig.saved`, transcreve (GPU em uso — `nvidia-smi` mostra
faster-whisper/ctranslate2).

- [ ] **Step 3: Monitorar progresso**

```bash
watch -n 30 'docker exec devcontainer-rabbitmq-1 rabbitmqctl list_queues name messages | grep ig.saved'
curl -s http://127.0.0.1:8084/ig/progress | head -c 400
curl -s http://127.0.0.1:8084/knowledge/documents | head -c 400
```
Expected: contador `ig.saved` caindo; documentos crescendo.
**Volume/decisão:** se a fila demorar demais ou o processo parecer de
produção sensível, PAUSAR (`docker compose stop worker` ou
`POST /ig/worker/stop`), registrar no HANDOFF e perguntar ao usuário —
parada proposital é opção válida (já usada antes).

- [ ] **Step 4: Validação fim a fim**

```bash
curl -s "http://127.0.0.1:8084/knowledge/search?q=&top_k=3" | head -c 500
curl -s "http://127.0.0.1:8084/knowledge/documents" | python3 -c "import json,sys; d=json.load(sys.stdin); print('docs:', d.get('total', len(d.get('results', []))))"
```
Expected: docs novos pesquisáveis; amostra de post pós-fila com
transcrição. Contagem final registrada.

- [ ] **Step 5: Decisão de estado + HANDOFF**

Decidir (com usuário): worker continua rodando (produção) ou é parado.
HANDOFF Task 7 ✅ com contagens antes/depois; linha 7.

---

### Task 8: Documentação

**Files:**
- Modify: `insta_kb/docs/HANDOFF.md`, `minimax/docs/HANDOFF.md`,
  `minimax/docs/REVISAO_2026-10-07.md` (apêndice), READMEs (ambos),
  `minimax/docs/MCP_TOOLS.md` (regenerar)
- Create/Update: seção de versões finais em cada README

**Interfaces:**
- Produces: docs consistentes com o estado final; CI verde.

- [ ] **Step 1: Regenerar MCP_TOOLS.md**

```bash
cd $HOME/tools-local/minimax-video-factory
uv run python scripts/generate_mcp_docs.py   # (--help se pedir args)
git diff --stat docs/MCP_TOOLS.md
```
Expected: regenerado refletindo fastmcp 4 / tools da Task 5.

- [ ] **Step 2: HANDOFFs finais + REVISAO**

Em ambos HANDOFF: tabela final de tasks ✅, versões finais (av, fastmcp,
fastapi, ComfyUI v0.39.1, uv), decisões novas, pendências remanescentes.
REVISAO_2026-10-07.md: apêndice "atualização 2026-10-07" com os números dos
renders e a matriz do av. READMEs: tabela de stack/versões atualizada.

- [ ] **Step 3: Validar builds de docs e CI**

```bash
cd $HOME/tools-local/minimax-video-factory && uv run mkdocs build --strict
```
(insta_kb não tem mkdocs — validar só ruff/pyright/pytest, que já rodam no
CI). Expected: build sem warnings; commit + push (pedir OK antes do push)
para CI verde no GitHub.

- [ ] **Step 4: Commit + HANDOFF**

Commits de docs (um por repo). HANDOFF Task 8 ✅; linha 8.

---

### Task 9: Diagramas dos 2 projetos

**Files:**
- Create: `insta_kb/docs/ARQUITETURA.md`,
  `minimax/docs/ARQUITETURA.md` (diagramas Mermaid)
- Modify: README de cada repo (link/âncora)
- Optional: `graphify-out/` em cada repo (com OK do usuário)

**Interfaces:**
- Produces: 3 Mermaid por repo (estrutura, stack/libs, fluxo) renderizando
  no mkdocs/GitHub; grafo graphify opcional.

- [ ] **Step 1: minimax/docs/ARQUITETURA.md**

Conteúdo mínimo (preencher com o estado final da Task 8):

````markdown
# Arquitetura — minimax-video-factory

## Estrutura
```mermaid
graph TD
  subgraph repo[src/]
    server[minimax_mcp/server.py — FastMCP 12 tools] --> comfy[comfyui_client]
    server --> wf[workflows/*.json H3 t2v/r2v/turbo]
    server --> trans[transcriber.py faster-whisper+av 18.1.0]
  end
  subgraph docker[docker/]
    img[minimax-comfyui:local — ComfyUI v0.39.1 + /opt/mcp-venv]
    mcp2[mcp service — streamable-http 8848]
  end
  oc[OpenCode] -->|4 instâncias MCP| server
  oc -->|docker exec| img
  comfy --> out[output/*.mp4]
```

## Stack
```mermaid
graph LR
  infra[Docker + GPU nvidia / RTX 4080 12GB] --> comfy[ComfyUI v0.39.1 + MiniMax H3 int8/nvfp4]
  infra --> py[Python 3.14 + uv]
  py --> fastmcp[fastmcp 4.0.11] --> oc[OpenCode MCP]
  py --> fw[faster-whisper 1.2.1 + av==18.1.0]
  py --> yt[yt-dlp]
```

## Fluxo
```mermaid
sequenceDiagram
  participant U as Usuário/OpenCode
  participant M as MCP server
  participant C as ComfyUI :8188
  U->>M: generate_video/submit_scene
  M->>C: POST /prompt (workflow H3)
  C-->>M: prompt_id / progresso
  M-->>U: mp4 em output/
```
````

- [ ] **Step 2: insta_kb/docs/ARQUITETURA.md**

Mesmo padrão, conteúdo: estrutura (`api/`, `app/`, `core/knowledge`,
`infra/{db,queue,instagram}`, `mcp_server/`, `workers/`); stack
(Postgres+pgvector 5433, RabbitMQ 5673, FastAPI 8084, Ollama, fastmcp 4,
faster-whisper+av 18.1.0, Docker `insta-kb-net`); fluxo de sequência
`fila ig.saved → ig_worker (GPU transcribe) → ingest → KB → /knowledge/search`.

- [ ] **Step 3: Links nos READMEs + validação de render**

Adicionar seção "Arquitetura → docs/ARQUITETURA.md" no README de cada repo.
`uv run mkdocs build --strict` (minimax); preview dos 3 diagramas de cada
repo (mkdocs serve ou GitHub preview). Expected: sem erro de sintaxe Mermaid.

- [ ] **Step 4: graphify (opcional, com OK)**

Se o usuário confirmar: invocar skill `graphify` em cada repo (gera
`graphify-out/`); registrar no HANDOFF. Se não, pular — Mermaid já cobre o
pedido.

- [ ] **Step 5: Commit final + HANDOFF + plano**

Commit docs nos 2 repos. HANDOFF Task 9 ✅ — HANDOFFs mostram TODAS as tasks
completas. Marcar linhas 8-9 e sincronizar as 3 cópias do plano (tabela toda
✅).

---

### Task 10: Auditoria SOLID (avaliação de refatoração)

**Files:**
- Read: `insta_kb/src/**` (~5,3k linhas), `minimax/src/**` (~1,8k linhas)
- Create: `insta_kb/docs/SOLID_AUDIT.md`, `minimax/docs/SOLID_AUDIT.md`
  (estrutura idêntica nos 2)
- Modify: HANDOFF de cada repo (seção resumo + linha 10 da tabela)
- **Nenhum arquivo de código alterado.** Ferramentas só via `uvx`
  (one-off) — zero mudança em `pyproject.toml`/`uv.lock`.
- Design aprovado: `insta_kb/docs/superpowers/specs/2026-10-07-solid-audit-task-design.md`

**Interfaces:**
- Consumes: baselines das Tasks 1-2 (168 / 9 passed — **citadas no
  relatório, não re-executadas**) e o estado final das Tasks anteriores.
- Produces: backlog priorizado (severidade × esforço) + veredito
  **go/no-go por repo**. Análise estática: **sem GPU**, execução livre
  em relação às outras tasks (não briga com 4/6/7).

- [ ] **Step 1: Métricas one-off (sem tocar no lock)**

```bash
cd $HOME/localhost/insta_kb
uvx radon cc src -s -j | tail -40      # complexidade ciclomática
uvx radon mi src -j | tail -20          # manutenibilidade
uvx vulture src --min-confidence 80     # dead code
# grafo de imports/ciclos: script AST descartável em /tmp (não commitar;
# pydeps opcional via uvx se necessário)
cd $HOME/tools-local/minimax-video-factory
uvx radon cc src -s -j | tail -30 && uvx radon mi src -j | tail -15
uvx vulture src --min-confidence 80
```
Expected: ranking de funções complexas + dead code por repo; nenhum
artefato versionado além do relatório.

- [ ] **Step 2: Checklist S/O por módulo grande**

Aplicar nos piores escores do Step 1 (já conhecidos: `ig_worker.py`
1031 linhas, `infra/llm/client.py` 834, `core/knowledge/knowledge.py`
732, `minimax_mcp/server.py` 433, `orchestrator.py` 341):
- **S** — responsabilidades múltiplas por módulo/função (ex.:
  download+transcrição+ingest+orquestação num mesmo arquivo).
- **O** — cadeias if/elif sobre tipos (seleção de workflow, transports)
  e extensibilidade dos MCP tools sem modificar código existente.

- [ ] **Step 3: Checklist L/I/D + acoplamento**

- **L** — hierarquias reais (transports do fastmcp, device paths do
  transcriber); registrar `N/A` quando não houver hierarquia.
- **I** — clientes monolíticos (`llm/client`, `comfyui_client`),
  contagem de tools por serviço (cada tool = contrato?).
- **D** — grafo de imports: camada alta importando infra direto
  (ig_worker → instagrapi/db/ollama), **ciclos**, tangle por módulo.

- [ ] **Step 4: Síntese — backlog + veredito**

Findings classificados **crítico / importante / menor / cosmético**
(cosméticos só em apêndice — não poluem o relatório); backlog final
ordenado por **severidade × esforço**; veredito **go/no-go por repo**
(refatorar agora / deixar p/ ciclo futura / não precisa).

- [ ] **Step 5: Entregável + HANDOFF + plano**

`docs/SOLID_AUDIT.md` nos 2 repos com os blocos (métricas, S/O, L/I/D,
acoplamento, backlog, veredito). **Guardrail:** nenhum path `/home/<user>`
no relatório (repo minimax é público; `tests/unit_privacy.py` derruba a
suíte). Seção resumo + ponteiro no HANDOFF de cada repo; linha 10 da
tabela nas 3 cópias do plano (md5 sync). Commits separados por repo.

---

## Riscos e rollbacks

| Risco | Mitigação/rollback |
|---|---|
| fastmcp 4 quebra server minimax | TDD + revert do pin (lock anterior) |
| ComfyUI v0.39.1 quebra node/peso | `COMFY_IMAGE=minimax-comfyui:v0.30.2-backup` sem rebuild |
| av 18.1.0 inesperado | revert do pin; smoke reproduzível (Task 1 Step 5) |
| OOM de VRAM | 1 job GPU por vez; pré-checks `nvidia-smi` |
| `.mcp.json`→HTTP corta tools | aviso + reload Task 5 antes do teste Task 6 |
| Download errado de 21 GB | checkpoint OK obrigatório (Task 3 Step 6) |
| Fila 329 msgs longa demais | parada proposital + decisão registrada |
| Bind-mount esconde venv da imagem | Task 4 Step 2 decide comando (uv run) |
| ctranslate2 só CPU no container | registrar; fallback worker no host (decisão no HANDOFF) |

## Pendências conhecidas (fora de escopo — não perguntar de novo)

- Autenticação da API REST: aceita, só `127.0.0.1` por ora.
- `transcriber.py` não é compartilhado entre os repos (decidido).
- `minimax_h3_experiments/sol_test1`: experimental, só se pedido.
- `minimax-knowledge-base` no opencode.json aponta pro server.py antigo do
  video-factory (sem tools de KB) — observar se deve ser removido/rebatizado
  na Task 5; não é bloqueio.
