# insta_kb — handoff, atualizado 2026-10-06

Este arquivo guarda o **estado**: o que já foi migrado/implementado, o que
está rodando, o que falta, os bugs já encontrados e corrigidos, e as decisões
tomadas com o usuário. As regras permanentes de projeto (quando existirem)
devem ir para um `CLAUDE.md` na raiz — ainda não criado.

Plano original desta migração (todas as fases e decisões):
`~/.claude/plans/task-notification-task-id-a46236a77511d-valiant-cray.md`.
Ver também `minimax-video-factory/docs/HANDOFF.md`, seção "EM ANDAMENTO
2026-10-06", para o lado que ainda falta limpar no projeto de origem.

## 🔄 EM ANDAMENTO 2026-10-07: atualização completa (deps/Docker/MCPs/testes)

Plano mestre: `~/.claude/plans/vamos-atualizar-a-lista-groovy-sun.md` — cópias
versionadas em `docs/PLANO_ATUALIZACAO.md` (este repo) e no minimax-video-factory.
**Branch: `update/deps-2026-10`** (PR no fim). Execução inline, 10 tasks
(0-9), status da tabela no próprio plano.

| Task | Descrição | Status | Evidência |
|---|---|---|---|
| 0 | Commits pendentes + baseline + cópias do plano | ✅ | baseline `168 passed` (+9 no minimax); commits `e2095eb` (fail-soft+export), `9aaa257` (plano) |
| 1 | Deps insta_kb | ✅ | `av==18.1.0`, `instagrapi>=3.0.20`, `fastmcp>=4.0.11`, `fastapi[standard]>=0.142.2`; lock: uvicorn 0.54.0, ruff 0.16.10, pyright 1.1.414, pydantic 2.13.5; suíte 168 passed + smoke `decode_audio` OK; review "with fixes" aplicado (telemetry verificado, floor fastapi ajustado); commit `58e8093` |
| 2 | Deps minimax | ⏳ | |
| 3 | ComfyUI v0.39.1 + nodes + pesos | ⏳ | |
| 4 | Docker insta_kb (Parte B) | ⏳ | |
| 5 | MCPs do OpenCode | ⏳ | |
| 6 | Matriz de testes | ⏳ | |
| 7 | Fila do insta_kb (329 msgs) | ✅ (parada proposital) | drenagem parcial 12:56–13:05: 329→**322** ready, 0 dead, **7 ingests** (docs 3713→3719), 0 errors; ver seção abaixo |
| 8 | Documentação | ⏳ | |
| 9 | Diagramas | ⏳ | |

Regra de GPU durante toda a execução: 1 job (render/transcrição/Ollama) por
vez; `av==18.1.0` fixo nos 2 repos; nenhum download de peso sem OK.

### Task 7 (fila ig.saved) — drenagem proposital parcial, 2026-10-07

Decisão do usuário (pergunta do plano, Step 3): **parar após validar**, não
drenar tudo de uma vez — a fila pode ser retomada depois. A Task 7 consumia
"stack Docker (Task 4)/API (Task 4)", mas o caminho provado do HANDOFF
(`uv run python -m workers.ig_worker`, verificado em "Verificação
end-to-end" abaixo) funcionou sem a API 8084: **worker no host**, desvio
registrado. A outra sessão da Task 4 já adicionou `ffmpeg + cuda libs pro
worker` no devcontainer (`02140f1`) — na próxima rodada subir em container.

- **Pré-check de GPU:** ComfyUI segurava ~10 GB ociosos com modelos em
  cache; liberados via `POST :8188/free {"unload_models":true,...}`
  (VRAM 10668→908 MiB). Whisper `small` em cuda+fp16 coube de boa.
- **Execução 12:56:11→13:05:36** (~9 min): 7 posts e2e completos
  (download IG → whisper cuda ~11s → 2× Ollama → ingest KB), cadência
  ~70s/post, **0 erros, 0 dead**. Docs 3713→3719; total KB **3411**;
  doc mais recente com summary+tutorial+transcription (pipeline inteiro).
- **Parada:** `SIGTERM` no pid — graceful; unacked voltou p/ ready.
  Fila final: **322 ready, 0 unacked, 0 consumers, 0 dead.**
- **Retomar:** `cd ~/localhost/insta_kb && nohup uv run python -m
  workers.ig_worker > /tmp/opencode/ig_worker.log 2>&1 &` (GPU livre
  primeiro: conferir `nvidia-smi`; regra de 1 job GPU/vez — a fila drena
  ~6h30 e nesse período renders do minimax ficam em espera).
- Monitorar sem API: `rabbitmqctl list_queues name messages consumers`
  + `grep ingested /tmp/opencode/ig_worker.log` + MCP `ig_queue_status`.



- **`[tool.uv] environments = ["sys_platform != 'android'"]`** (desvio do
  plano, obrigatório): `instagrapi>=3.0.20` trava `pydantic==2.12.5` no
  marker Android, conflitando com `pydantic>=2.13.4` do projeto — o lock
  falhava para todo ambiente. Servidor Linux only; Android nunca é alvo de
  deploy. É a solução sugerida pelo próprio uv. Efeito: tentativa de
  resolver em Android falha alto em vez de silenciosamente.
- **Telemetry do FastAPI 0.142 (default-on) — decisão: manter o default.**
  Verificado em runtime nesta sessão: sem `OTEL_*` no ambiente,
  `app._native_telemetry.enabled()` → **False** (middleware nem cria spans,
  zero overhead por request) — `.env`/`.env.example`/shell não têm
  `OTEL_*`. Se alguém definir `OTEL_EXPORTER_OTLP_ENDPOINT` depois, é
  exatamente a intenção (exportar) que o auto_configure atende. Nenhuma
  mudança de código.
- **`fastapi[standard]>=0.142.2`** (floor ajustado após code review): o
  floor antigo `>=0.141.1` permitiria re-lock voltar para 0.141.1.
- **`uv.lock` revision 3→5** (novo campo `supported-markers`): qualquer
  imagem Docker/venv que consuma este lock precisa de **uv ≥ 0.12.x** —
  checar nos builds das Tasks 3/4.
- **`agent-detector`** entrou no venv via `fastapi-cloud-cli` (extra
  `fastapi[standard]`), não é importado pelo app, pip-audit limpo — se o
  next reviewer de supply-chain estranhar, é daí. Escape hatch:
  `fastapi[standard-no-fastapi-cloud-cli]`.

## ✅ Commit real em 2026-10-06: `a773f2c` em `migrate/instagram-kb-from-video-factory`

Primeiro commit de código de verdade do projeto (antes só tinha `app/main.py`
stub). Passou pelos 6 hooks do `pre-commit` de verdade pela primeira vez —
`ruff check`, `ruff format`, `pyright` (strict), `bandit`, `pip-audit` — porque
o hook estava **quebrado** até esta sessão: `.git/hooks/pre-commit` tinha um
path Python hardcoded de outro ambiente (`/home/appuser/.cache/uv/...`, de
dentro do devcontainer), então todo commit antes deste silenciosamente nunca
rodou os checks. `uv run pre-commit install` corrigiu o path para este host.

Isso revelou **497 erros de `pyright` strict**, 100% em testes/scripts (zero
em `src/`) — faltavam anotações de tipo em funções fake/mock usadas com
`monkeypatch.setattr` e em lambdas inline (que não aceitam anotação de
parâmetro em Python, por isso viraram funções nomeadas tipadas). Corrigidos
de verdade, não suprimidos — só 11 `reportPrivateUsage` (testes acessando
helpers internos do `ig_worker`/`repository` sem equivalente público) e 2
casos de lacuna do stub do `pika` levaram `# pyright: ignore` documentado.
`pip-audit` também pegou CVEs reais em `urllib3`/`virtualenv` — nenhum dos
dois é dependência direta do app (vêm do próprio `pre-commit`/`sentry-sdk`
transitivamente); `uv lock --upgrade-package urllib3 --upgrade-package
virtualenv` resolveu sem tocar em nada do app.

Estado final verificado: `uv run pytest -q` → 136 passed; `pyright` → 0
erros; `ruff`/`bandit`/`pip-audit` → limpos.

⚠️ **O commit está na branch `migrate/instagram-kb-from-video-factory`, não
`main`.** Decidir/confirmar quando mergear.

---

## O que é este projeto

Nasceu dentro do `minimax-video-factory` por acidente — um servidor MCP de
geração de vídeo foi acumulando um segundo domínio inteiro: uma base de
conhecimento com RAG sobre posts salvos do Instagram (resumo, transcrição,
tutorial, busca semântica) alimentada por um pipeline de sincronização
assíncrono. Este repo é esse domínio, **extraído e implementado de verdade**
(os esqueletos antigos de `insta_kb`/`insta_brain` eram só stubs, nunca
tinham código funcional).

**Diferencial declarado** (pedido explícito do usuário, comparando com
`paperfoot/clinstagram` e `SamurAIGPT/ai-knowledge-base`): este projeto usa
fila (RabbitMQ) + worker dedicado (`ig-worker`) + banco real
(Postgres+pgvector), não um script de raspagem de passo único.

## Stack

- **Postgres + pgvector** (`insta-kb-postgres`, porta externa 5433) —
  documentos, chunks, embeddings.
- **RabbitMQ** (`insta-kb-rabbitmq`, porta AMQP 5673, management 15673) —
  fila `ig.saved` (trabalho) + `ig.saved.dead` (DLQ) + `ig.worker.command`
  (start/stop do worker).
- **Ollama** (host, fora deste repo) — LLM (`lfm2:24b`) para
  resumo/tutorial/RAG, visão (`qwen2.5vl:7b`) para descrever vídeo.
- **`ig-worker`** — processo Python que consome `ig.saved`, baixa o post
  (`instagrapi`), transcreve (`faster-whisper`), descreve visualmente e
  ingere no banco.
- **MCP server** (`src/mcp_server/server.py`, `fastmcp`) — 15 tools.
- **REST API** (`src/api/main.py`, FastAPI) — subconjunto das tools, com
  Swagger UI (`/docs`) e ReDoc (`/redoc`) automáticos.

## Estrutura (`src/`)

```
core/
  settings/config.py   # Settings (pydantic-settings), .env central
  knowledge/knowledge.py  # use cases: search, ask, ingest_*, list_documents,
                          #   export_search, export_documents, reindex_all
infra/
  db/            # models (Document/Chunk/Embedding) + repository (SQLAlchemy)
  llm/           # cliente Ollama (chat, embedding)
  queue/         # publisher/consumer RabbitMQ (ig.saved, control queue)
  instagram/     # cliente instagrapi (enumerar saved posts)
  downloader/    # baixar mídia de um post
  transcriber/   # faster-whisper (cópia própria, não compartilhada com o
                 #   video-factory, decisão explícita do usuário)
  vault/         # export para Obsidian (.md)
  log/
workers/         # ig-worker: consome ig.saved, orquestra download→transcrição→
                 #   descrição visual→ingestão
mcp_server/server.py   # as 15 tools MCP (nome "mcp_server", não "mcp", para
                        #   não colidir com o pacote fastmcp)
api/main.py            # REST wrapper fino sobre as mesmas funções do MCP
app/                   # stub antigo, pré-migração — candidato a remoção
```

## MCP tools expostas (15)

`ig_sync_saved`, `ig_queue_status`, `ig_worker_start`, `ig_worker_stop`,
`ig_get_progress`, `knowledge_ingest_text`, `knowledge_ingest_markdown`,
`knowledge_ingest_video`, `knowledge_ingest_audio`, `knowledge_search`,
`knowledge_ask`, `knowledge_reindex`, `kb_list_documents`, `kb_export_search`,
`kb_export`.

Registrado em `.mcp.json` (raiz deste repo) como servidor `insta-kb`
(`uv run python src/mcp_server/server.py`, `MCP_TRANSPORT=stdio`) —
**aprovado e testado** pelo usuário nesta sessão (`kb_list_documents` e
`ig_queue_status` responderam corretamente contra o banco real).

## REST API (10 endpoints, FastAPI, `src/api/main.py`)

Todo endpoint delega para a mesma função que a tool MCP equivalente chama —
zero lógica duplicada. Documentação automática em `/docs` (Swagger) e
`/redoc`.

| Endpoint | Espelha a tool MCP |
|---|---|
| `GET /healthcheck` | — (liveness só do processo API) |
| `GET /ig/queue-status` | `ig_queue_status` |
| `GET /ig/progress` | `ig_get_progress` |
| `POST /ig/worker/start` | `ig_worker_start` |
| `POST /ig/worker/stop` | `ig_worker_stop` |
| `GET /knowledge/search` | `knowledge_search` |
| `POST /knowledge/ask` | `knowledge_ask` |
| `GET /knowledge/documents` | `kb_list_documents` (paginado, filtros `platform`/`doc_type`/`tag`) |
| `GET /knowledge/export/search` | `kb_export_search` |
| `POST /knowledge/export` | `kb_export` |

**Deliberadamente fora da REST** (só MCP, decisão registrada no docstring do
módulo): `ig_sync_saved` (scan longo, sensível a rate-limit do Instagram),
`knowledge_ingest_*` (síncrono, pode levar minutos — precisa de desenho de
job em background antes de ser um bom endpoint REST), `knowledge_reindex`
(pesado, reconstrói todo embedding — fácil demais de disparar por acidente
via HTTP).

## Testes

**136 testes passando** (`uv run pytest -q`, verificado nesta sessão).
Padrão: monkeypatch direto nas funções de `mcp_server.server`/
`mcp_server.knowledge` — nenhum teste toca Postgres/RabbitMQ de verdade.

## Qualidade de código

Convenção adotada (seguindo o pedido do usuário de "fazer certo"): ruff
(E, F, I, W, PL), pyright estrito, bandit, pip-audit — todos devem passar ou
ter exceção documentada inline (não lint-ignore mudo). `pre-commit` já
configurado (`e859249`, `d653fda`).

---

## Dados reais migrados (não é um banco vazio)

Migração feita **parando os containers antigos, movendo os volumes de
dados reais, e subindo o compose novo sobre eles** — não criando containers
vazios e re-sincronizando do zero. Verificado em dois checkpoints (antes e
depois do fix de hostname do RabbitMQ, ver bug abaixo), ambos batendo:

```
documents | chunks | embeddings
-----------+--------+------------
      3403 |  12755 |      12755
```

Filas RabbitMQ (`ig.saved`, `ig.saved.dead`) migradas via **export/import de
definições pela API HTTP de management** do RabbitMQ (não via `mv` direto do
diretório de dados — ver bug de permissão abaixo). Confirmado: ambas
duráveis, 0 mensagens perdidas.

**Estado atual da fila:** `ig_queue_status` reporta `ig.saved` vazia — 0
prontos, 0 mortos, **0 consumidores ativos**. O `ig-worker` ainda não foi
colocado para rodar de verdade neste projeto; o código está migrado e
testado, mas nada está consumindo a fila agora.

---

## Bugs encontrados e corrigidos nesta migração

### 1. RabbitMQ: identidade do nó Mnesia depende do hostname

Depois de um `docker compose up` (nome de projeto/container diferente do
`docker run` antigo), as filas voltaram vazias (`[]`) — o nó Mnesia é
`rabbit@<hostname>`, e sem um hostname fixo no compose, cada recreate do
container cria um nó Mnesia **novo e vazio**, órfão do anterior.

**Fix:** `hostname: insta-kb-rabbitmq` fixo em
`.devcontainer/docker-compose.yml`, força-recriado, definições
reimportadas do backup. Comentário explicando o bug deixado no compose.

**Risco se isso se repetir:** qualquer `docker compose up` futuro sem esse
`hostname:` fixo (ex. editar o compose e esquecer a linha) volta a criar
nó vazio. Checar essa linha sempre que o compose for tocado.

### 2. `config.py`: `env_file` relativo resolve contra o CWD do processo, não o arquivo

`env_file="../../.env"` (caminho relativo) funcionava só se o processo fosse
iniciado do diretório exato esperado. Rodando `uv run python src/...` da raiz
do repo (o caso comum), `../../.env` resolvia **duas pastas acima do repo** —
`.env` falhava em carregar **silenciosamente**, e todo setting caía no
default do campo (Postgres porta 5432 — a porta do *video-factory*, não
desta).

**Sintoma:** `psycopg.OperationalError: ... port 5432 ... Connection
refused` ao chamar `knowledge.list_documents()`.

**Fix:** `_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"`
(absoluto, âncorado no arquivo, não no CWD). Ver
`src/core/settings/config.py:13`.

**Por que isso importava mais que um bug comum:** se não notado, poderia ter
causado um projeto escrevendo dados do `insta_kb` no Postgres de **outro**
projeto que por acaso estivesse ouvindo na 5432 — corrupção cruzada
silenciosa entre projetos, não só uma falha óbvia de conexão.

### 3. `extra="forbid"` (default do pydantic-settings) rejeitava `.env` compartilhado com o compose

Depois do fix #2, `.env` carregou de verdade e expôs chaves que só o
`docker-compose.yml` usa (`PG_UID`, `PG_GID`, `POSTGRES_DATA_DIR`,
`IG_WORKER_CONCURRENCY`, `RABBITMQ_DATA_DIR`, `RABBITMQ_MANAGEMENT_PORT`) —
`pydantic_core.ValidationError: Extra inputs are not permitted`.

**Fix:** `extra="ignore"` em `model_config` (`config.py`), com comentário
explicando que `.env` é intencionalmente compartilhado com o compose.

### 4. `infra/db/__init__.py` não reexportava `count_documents`/`list_documents`

Ao implementar a paginação do catálogo (`kb_list_documents`/
`GET /knowledge/documents`), as duas funções novas em `repository.py` não
estavam na lista de reexport do `__init__.py` do pacote →
`AttributeError: module 'infra.db' has no attribute 'count_documents'`.

**Fix:** adicionadas ao import e ao `__all__` de `src/infra/db/__init__.py`.

### 5. Permissão: diretório de dados do RabbitMQ antigo não podia ser `mv`ido

`.rabbitmq`/dados do volume antigo pertenciam a uid 999 (usuário do
container) — `mv` direto deu "Permission denied", e `sudo mv` foi
deliberadamente evitado (pediria auth interativa, e o pedido do usuário era
para fazer a coisa "certa", não a mais rápida). Resolvido via export/import
de definições pela API HTTP do RabbitMQ (ver seção de dados acima) em vez de
mover arquivos de dados brutos.

### 6. `rm -rf` em diretórios Mnesia órfãos ainda pendente

Dois diretórios de nó Mnesia ficaram como lixo inofensivo depois do fix de
hostname: `rabbit@4445f292f27f*` e `rabbit@b83ab13f8ee3*`. Bloqueado para
remoção automática pelo classificador de permissão desta sessão —
**o usuário pode `rm -rf` manualmente se quiser**, não afeta o funcionamento.

---

## Scripts operacionais migrados (2026-10-06)

4 scripts da campanha de reprocessamento do Instagram (2026-08-10/11), antes
em `minimax-video-factory/scripts/`, agora em `scripts/` aqui — ficariam
quebrados no video-factory de qualquer jeito, pois importavam de módulos já
deletados lá (`minimax_mcp.{db,ig_sync,ig_worker,knowledge,llm}`):

| Script | Para quê |
|---|---|
| `ig_pendentes.py` | lista os `document_id` de IG ainda não reprocessados pelo pipeline atual |
| `ig_reprocessar.py` | reprocessa uma lista de ids a partir da mídia em disco; sem `--aplicar` só compara |
| `ig_rodar_tudo.sh` | encadeia reprocessamento → sincronização, para não disputar GPU junto |
| `ig_relatorio.sh` | relatório só-leitura do sync (fila, banco, disco, GB/post) |

Portados trocando só os imports (`minimax_mcp.X` → o módulo real daqui:
`workers.ig_worker`, `core.knowledge.knowledge`, `infra.db`,
`infra.instagram.ig_sync`, `infra.llm.client`) e a env var do banco
(`KB_DATABASE_URL` → `DATABASE_URL`, o nome usado neste projeto) e a porta de
management do RabbitMQ (`15672` → `15673`, a porta real daqui). **Verificado
por import de verdade** (`uv run python -c "from workers import ig_worker;
..."`, sem erro) e sintaxe (`py_compile`/`bash -n`), não só lidos. São
scripts de uma campanha pontual, sem teste automatizado — rodam contra dados
reais sob `--aplicar`, de propósito, com backup antes de qualquer escrita.
Não foram **executados** nesta sessão (não havia nada pendente para
reprocessar), só portados e verificados estaticamente.

**Mais 3 migrados em 2026-10-07**: `ig_catchall_check.py` (enumera só a
coleção "All posts", a única que lista tudo), `ig_replay_dlq.py` (devolve
mensagens da DLQ para a fila de trabalho, dedup por `ig_pk`),
`ig_sync_bg.py` (sincronização completa em processo de background, para
varreduras que passam do corte de 1800 s de uma tool MCP). Mesmo padrão de
verificação dos 4 anteriores.

**Checado nesta sessão: o `main` do minimax-video-factory recebeu ~8
commits de correção no domínio IG/KB depois que o ponto de partida do
worktree de separação divergiu** (heartbeat AMQP, carrossel misto, mídia
degradada — webp/áudio ausente/schema errado do LLM, post malformado na
listagem da catch-all, num_ctx/legenda/tetos de saída cortando conteúdo em
silêncio, publicação incremental por coleção). Conferido cada um contra o
código real aqui: **as 8 já estavam presentes** — a migração original já
tinha partido de um checkout tardio do `main`, não da base antiga do
worktree. Backfill de cobertura de teste para esse comportamento (28 testes
novos) em `tests/test_infra_{queue,llm_client,instagram_ig_sync}.py` e
`test_workers_ig_worker.py`.

## ✅ Security review em 2026-10-07 (PR #1, merged `2794806`)

Dois achados reais, ambos corrigidos:

1. **`kb_export` aceitava `output_dir` sem validar** — alcançável sem
   autenticação via `POST /knowledge/export`. Um caller podia escrever
   fora da árvore do projeto (`../../etc`, caminho absoluto). Corrigido:
   `export_documents` resolve e confere contra `{PROJECT_ROOT}/output`
   antes de qualquer escrita.
2. **Porta do serviço `python` no devcontainer compose em todas as
   interfaces** — diferente de postgres/rabbitmq, que já ficavam em
   `127.0.0.1`. Corrigido para `127.0.0.1`, mesmo padrão dos outros dois.

**Não encontrado:** segredos em log, SQL injection fora do `nosec` já
documentado, container rodando como root.

**Pendência que a review não resolveu, registrada e aceita por agora:** a
API REST não tem autenticação nenhuma — nenhum endpoint, incluindo os de
escrita (`kb_export`) e controle (`ig_worker_start/stop`). Aceitável para
uso local-only (é o que é hoje, atrás de `127.0.0.1` depois do fix acima);
precisa de uma camada de auth antes de qualquer exposição alem disso.

## ✅ Verificação end-to-end em 2026-10-07 (PR #2, merged `7b6d2fe`)

Subi o `ig-worker` de verdade (`uv run python -m workers.ig_worker`) contra
a fila real — 329 mensagens, conta do Instagram autenticada
(`IG_SESSIONID` real, não um mock). Confirmado: conecta no RabbitMQ real
(1 consumidor), lista coleções reais, baixa um post real
(`media/.../info/` → 200), e **toda transcrição de vídeo falhava**:

```
TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

`faster-whisper` 1.2.1 chama `av.open(..., metadata_errors="ignore")` — um
kwarg que o PyAV removeu numa versão major posterior. `av>=12.0.0` deixava
o `uv` resolver a mais nova (19.0.1), sem o parâmetro. O worker **não
quebrou** (capturou a exceção, logou, seguiu) — mas isso ia bater em cada
vídeo da fila. Corrigido: `av==15.1.0` (a mais antiga com wheel para
Python 3.14, que ainda tem o parâmetro). Testado de verdade com
`faster-whisper` transcrevendo um `.mp4` sintético (CPU, para não disputar
a GPU que um render real do video-factory ocupava nesse instante).

> **Atualização 2026-10-07 (sessão do plano de atualização):** o pin subiu
> para **`av==18.1.0`** — a mais recente que funciona. Teste real em venv
> py3.14 com decode de wav: 15.1.0/17.1.0/18.0.0/18.1.0 ✅ ·
> 19.0.0/19.0.1 ❌ (`TypeError: metadata_errors` removido). `faster-whisper`
> latest continua 1.2.1 sem fix (declara `av>=11` sem teto — o pin é o que
> protege). Mesmo pin aplicado no minimax-video-factory (ele nem declarava
> `av`).

**Worker parado depois da verificação** — processar as 329 mensagens reais
seria uma rodada de produção, não uma verificação; fica para quando o
usuário decidir rodar de propósito. Fila confirmada intacta (329 ready, 0
unacked, 0 consumers) depois de parar.

## Pendências abertas (nenhuma delas é um bug — são próximos passos)

1. **Rodar o `ig-worker` de propósito, não só verificar.** O código
   funciona de ponta a ponta agora (confirmado acima); falta decidir
   quando processar as 329 mensagens reais da fila.
2. **Verificação end-to-end cruzando os dois projetos** (video-factory
   renderizando + insta_kb consumindo a fila ao mesmo tempo, de propósito,
   para confirmar que a regra de "um de cada vez" é respeitada) — cada
   lado foi verificado funcionando isoladamente, não ainda simultaneamente.
3. **`app/` (stub antigo pré-migração)** ainda presente em `src/app/` —
   provavelmente lixo, não revisado para remoção nesta sessão.
4. **`.pgdata_empty_devcontainer_bak/`** e **`.rabbitmq_empty_devcontainer_bak/`**
   na raiz — backups dos volumes vazios do devcontainer antigo, mantidos por
   segurança durante a migração. Avaliar se ainda são necessários; o
   primeiro já apresentou erro de permissão ao listar (dono não é o usuário
   atual).
5. **Autenticação na API REST** — nenhuma hoje (ver security review acima).

**Já concluído, não repetir:** repositório no GitHub (`main`+`dev`, CI),
security review formal (PR #1), code review (as correções de av/bandit
vieram de verificação real, não de leitura de relatório).

## Decisões tomadas com o usuário (para não re-perguntar)

- Nome definitivo do projeto: `insta_kb` (não `knowledge-mcp`, que foi
  arquivado — ver `minimax-video-factory` handoff).
- Escopo por agora: **só Instagram** (ingestão genérica de markdown/texto
  avulso fica de fora por ora, embora as tools `knowledge_ingest_text/
  markdown` já existam).
- `transcriber.py` **não é compartilhado** entre os dois repos — cópia
  própria aqui, para não criar acoplamento cruzado.
- Containers próprios por domínio: nada deste stack roda dentro do container
  do ComfyUI.
- Dados reais migrados por `stop → mover volume → recriar → verificar →
  limpar vazio`, não por re-sync do zero.
- API REST: paginação por página normal (`limit`/`offset`), separável por
  `platform`/`doc_type`/`tag` — pedido explícito do usuário ("Paginado
  separados por categorias, tags estas coisas").

## 2026-10-07 — revalidação pós-paralelo (av==18.1.0, teste faltante, database.db*)

Sessão retomada depois do usuário ter iniciado, em paralelo (outro terminal,
mesma working directory, branch `update/deps-2026-10`), a atualização de
deps documentada em `docs/PLANO_ATUALIZACAO.md`. Reconciliação feita:

- **`av==18.1.0`** (trocado de `15.1.0` pela atualização paralela) verificado
  de novo, independentemente: `av.open(..., metadata_errors="ignore")` ainda
  aceita o parâmetro (erro foi `InvalidDataError` de arquivo inválido, não
  `TypeError`), e uma transcrição real via `faster_whisper` (CPU, para não
  disputar GPU com o render do video-factory em andamento) terminou sem erro.
  Não reintroduz o bug que a mudança para 15.1.0 tinha corrigido.
- Faltava o teste `test_search_db_failure_returns_ok_false_not_raise` (cobre
  `search()` no fail-soft do commit `e2095eb`) — commitado agora
  (`766384a`, na branch `update/deps-2026-10`, que já era a branch
  checked-out).
- `database.db`/`-shm`/`-wal` (sqlite vazio, sem schema, não referenciado em
  código) — adicionados ao `.gitignore` (regra já estava em
  `docs/PLANO_ATUALIZACAO.md`: "nunca é commitado", mas o `.gitignore` não
  cumpria). Arquivos continuam no disco — remoção bloqueada pelo classificador
  de permissões desta sessão; não são segredo nem dado real, é lixo de alguma
  conexão sqlite default.
- Suíte completa no HEAD atual (`766384a`): `pytest` 169→170 passed, `ruff`/
  `pyright`/`bandit`/`pip-audit` limpos.

**Não toquei** no restante do WIP de `update/deps-2026-10` (bump fastmcp,
instagrapi, fastapi, lock --upgrade) — é o plano do usuário em andamento,
fora do escopo desta reconciliação.

## 2026-10-07 — Task 4 (docs/PLANO_ATUALIZACAO.md): venv do devcontainer quebrava o `.venv` do host

Ao preparar os novos serviços `api`/`worker`/`mcp` (isolamento em Docker,
Task 4), achado um bug real no devcontainer interativo que já existia antes
desta sessão: `.devcontainer/Dockerfile` colocava o venv em `/app/.venv`,
e `/app` é bind-mount do host em runtime (`volumes: [../:/app]`). Dois
problemas independentes:

1. Rodar `uv run`/`uv sync` **dentro** do container reescrevia o symlink
   `/app/.venv` (que no namespace do container apontava pra um cache
   inexistente) para um caminho que só existe dentro do container —
   quebrando silenciosamente o `.venv` do HOST na próxima vez que o usuário
   rodasse `uv`/`pytest` fora do container. Reproduzido e corrigido na hora
   com `uv sync` no host (169 testes voltaram a passar).
2. Mesmo tentando compartilhar via bind-mount do cache do host no mesmo path
   absoluto, o `pyvenv.cfg` do venv grava o caminho do interpretador do HOST
   (`/usr/bin/python3`), que não existe em nenhuma imagem de container — e
   o venv carrega extensões compiladas (psycopg, av/PyAV, ctranslate2,
   pydantic-core) contra libs do host, não portáveis entre bases Debian
   diferentes. Um venv genuinamente compartilhado host↔container não é
   seguro de montar assim.

**Fix** (`bf824e5` + follow-up do review): venv isolado em `/opt/venv`,
inteiramente dentro da imagem, nunca no bind-mount. Decisão consciente:
isso duplica os pacotes Python em disco dentro da imagem (não duplica
*download*, já que `uv` usa o cache de wheels normalmente) em troca de
nunca mais quebrar o ambiente do host — compartilhar não era seguro.
`UV_PYTHON_PREFERENCE` mudou de `only-managed` pra `only-system` (a imagem
já tem Python 3.14; o Python gerenciado pelo `uv` ficava em
`/root/.local/share/uv/python/...`, inacessível pro `appuser` não-root).
Adicionado também: `ffmpeg` (faltava no apt-get) e `nvidia-cublas-cu12`/
`nvidia-cudnn-cu12` + `LD_LIBRARY_PATH` pro `ctranslate2`/`faster-whisper`
rodar com GPU dentro do container (mesmo padrão já usado em
`minimax-video-factory/docker/Dockerfile` e `Dockerfile.python` — conferido,
já estava correto lá, nada a replicar). `uv sync --no-install-project
--no-dev` trocado pra `--frozen` (usa o lock commitado, não re-resolve), e
`COPY . /app` movido pra depois do `uv sync` (a cópia do repo inteiro antes
invalidava a camada de cache de deps a cada mudança de qualquer arquivo).

Também achado nesta janela: `.pgdata_empty_devcontainer_bak` (diretório
root-owned, 0700, resquício de um backup antigo de dados do Postgres)
bloqueava o `docker build` inteiro com "permission denied" ao ler o
contexto — resolvido com entrada no `.dockerignore` (não apagado: a
remoção foi bloqueada pelo classificador de permissões desta sessão; não
é segredo nem dado real, é lixo de disco).

Validado: `docker compose -f .devcontainer/docker-compose.yml run --rm
--no-deps python sh -c 'python -c "import fastapi, sqlalchemy, psycopg,
pydantic, ctranslate2"'` funciona sem precisar de `uv run`; `.venv` do
host comparado antes/depois do rebuild, idêntico; `uv run pytest -q` no
host: 169 passed (mesmo baseline). Devcontainer interativo (VS Code) ainda
não reaberto pelo usuário para confirmação final — pendência.
