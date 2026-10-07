# insta_kb — handoff, atualizado 2026-10-06

Este arquivo guarda o **estado**: o que já foi migrado/implementado, o que
está rodando, o que falta, os bugs já encontrados e corrigidos, e as decisões
tomadas com o usuário. As regras permanentes de projeto (quando existirem)
devem ir para um `CLAUDE.md` na raiz — ainda não criado.

Plano original desta migração (todas as fases e decisões):
`~/.claude/plans/task-notification-task-id-a46236a77511d-valiant-cray.md`.
Ver também `minimax-video-factory/docs/HANDOFF.md`, seção "EM ANDAMENTO
2026-10-06", para o lado que ainda falta limpar no projeto de origem.

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

## Pendências abertas (nenhuma delas é um bug — são próximos passos)

1. **Subir o `ig-worker` de verdade.** Código migrado e testado, mas 0
   consumidores na fila agora — nada está processando `ig.saved`.
2. **Criar o repositório no GitHub** com documentação (pedido explícito do
   usuário, ainda não iniciado).
3. **Task #8 do plano** (não específica deste repo, mas atinge-o): regenerar
   `README.md`/docs de tools depois que a limpeza do lado video-factory
   terminar e as 4 referências por tool forem atualizadas nos dois repos.
4. **Security review formal** (skill `security-review`) ainda não rodada
   formalmente — checagens informais já feitas ao longo da migração
   (bandit limpo, pip-audit só com achados pré-existentes, `IG_SESSIONID`
   confirmado fora de `.env-example`/logs/commits), mas falta a passada
   dedicada antes de considerar o repo pronto para público.
5. **Code review final** (`/code-review` ou skill `requesting-code-review`)
   ainda não rodado.
6. **Verificação end-to-end cruzando os dois projetos** (video-factory
   gerando vídeo + insta_kb consumindo a fila ao mesmo tempo, sem contenção
   de GPU indevida) ainda não feita — só verificado cada lado isoladamente.
7. **`app/` (stub antigo pré-migração)** ainda presente em `src/app/` —
   provavelmente lixo, não revisado para remoção nesta sessão.
8. **`.pgdata_empty_devcontainer_bak/`** e **`.rabbitmq_empty_devcontainer_bak/`**
   na raiz — backups dos volumes vazios do devcontainer antigo, mantidos por
   segurança durante a migração. Avaliar se ainda são necessários; o
   primeiro já apresentou erro de permissão ao listar (dono não é o usuário
   atual).

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
