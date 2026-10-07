# Teste ponta a ponta: sync Instagram → fila → worker → DB → API/MCP

## Contexto
A branch `migrate/instagram-kb-from-video-factory` trouxe o pipeline completo de
ingestão do Instagram (antes parte do `minimax-video-factory`) para este repo,
junto com uma API FastAPI nova (`src/api/main.py`) e um servidor MCP atualizado
(`src/mcp_server/server.py`). Nada disso foi testado de ponta a ponta ainda —
só existem testes unitários mockados (`tests/`). O objetivo agora é: (1)
sincronizar os posts salvos novos do Instagram, (2) baixá-los e convertê-los em
documentos na base de conhecimento (Postgres+pgvector) via o worker real, e (3)
validar que a suíte de testes, a API HTTP e as ferramentas MCP funcionam sobre
esse fluxo real.

Pré-checagens já feitas (read-only):
- Postgres (`devcontainer-postgres-1`) e RabbitMQ (`devcontainer-rabbitmq-1`)
  já estão rodando (`docker ps`).
- O servidor MCP (`src/mcp_server/server.py`) já está rodando neste ambiente
  (é o que respondeu `kb_list_documents`/`ig_queue_status` nesta conversa).
- Não há worker (`ig_worker`) nem API (`uvicorn`/`fastapi`) rodando agora.
- `IG_SESSIONID` estava vazio no `.env` — o usuário já colou o valor
  (confirmado: 77 caracteres presentes, conteúdo não lido).
- Ollama está acessível em `localhost:11434`, mas o modelo de visão
  configurado (`OLLAMA_VISION_MODEL`) precisa ser confirmado como disponível
  (`gemma4:12b` apareceu na listagem; não confirmei `qwen2.5vl:7b`).
- `ig.saved` está vazia agora (`ready: 0, dead: 0, consumers: 0`).

Decisões do usuário:
- Resolver `IG_SESSIONID` colando o valor real (feito).
- Sincronizar apenas posts **novos** (`ig_sync_saved(reprocessar=False)`) — os
  3403 já existentes no banco são ignorados automaticamente (dedupe por
  `ig_pk`).
- Cobertura do teste: suíte pytest + API HTTP ao vivo + MCP tools ao vivo.

## Passo a passo

### 1. Sincronizar posts salvos (MCP, ferramenta já carregada)
- Chamar `mcp__insta-kb__ig_sync_saved(reprocessar=False)`.
- Checar quantos itens novos foram enfileirados via `ig_queue_status`
  (campo `ready`).
- Se a chamada falhar por sessão inválida/expirada, parar e avisar o usuário
  (não há fallback automático de login).

### 2. Subir o worker no host (fora do devcontainer)
- O worker (`src/workers/ig_worker.py`) precisa rodar no host porque depende
  de Ollama/Postgres acessíveis localmente (ver comentário em
  `ig_worker.py:27-31`).
- Rodar em background: `uv run python -m workers.ig_worker` (usar
  `run_in_background` já que é um daemon de longa duração).
- Observar os primeiros logs para confirmar que ele conecta ao RabbitMQ e
  começa a consumir `ig.saved`.

### 3. Acompanhar o processamento
- Poll periódico com `ig_queue_status` (ready deve cair a 0) e
  `ig_get_progress(last_n=10)` para ver itens processados/erros.
- Se `dead` > 0, inspecionar a causa (provavelmente visível nos logs do
  worker) antes de seguir.
- Parar o worker (`ig_worker_stop` via MCP, ou matar o processo em background)
  quando a fila esvaziar.

### 4. Validar que os novos documentos entraram no banco
- `kb_list_documents(limit=...)` e comparar `total` antes/depois (era 3403).
- `knowledge_search` / `knowledge_ask` com um termo relacionado a algum post
  novo, para confirmar que embeddings/índice estão OK.

### 5. Suíte de testes unitários
- `uv run pytest tests/` na raiz do repo.
- Reportar resumo (passed/failed) e investigar qualquer falha antes de seguir
  (pode indicar que a API nova ou o MCP atualizado quebraram algo que os
  testes mockados cobrem).

### 6. API HTTP ao vivo
- Subir localmente: `uv run fastapi dev src/api/main.py` (ou `uvicorn
  api.main:app --reload`) em background, numa porta livre.
- Exercitar com `curl`/`httpx`:
  - `GET /healthcheck`
  - `GET /ig/queue-status`, `GET /ig/progress`
  - `GET /knowledge/documents`, `GET /knowledge/search?query=...`
  - `POST /knowledge/ask`
  - Confirmar que os endpoints de escrita (`ig_sync_saved`,
    `knowledge_ingest_*`, `knowledge_reindex`) continuam **não** expostos via
    REST (decisão de design já documentada no código).
- Encerrar o processo da API ao final do teste.

### 7. MCP tools ao vivo
- Já em uso nesta sessão. Exercitar adicionalmente, com o servidor MCP já
  rodando: `kb_export_search`, `kb_export` (gerar um export pequeno em
  `output/kb-export/`), `knowledge_reindex` (opcional, pode ser custoso —
  perguntar antes se for rodar em todo o índice).

## Riscos / pontos de atenção
- Não há migrations/`create_all` para o schema do Postgres — se o schema não
  existir ainda no banco, `save_document`/`search_documents` vão falhar. Como
  `kb_list_documents` já retornou 3403 documentos, o schema já existe e está
  populado — risco baixo, mas vale observar erros de schema no passo 2-3.
  Não é atribuição deste plano criar migrations.
- `IG_WORKER_MIN_INTERVAL` pode limitar a taxa de processamento (rate
  limiting deliberado do Instagram); se houver muitos itens novos, o passo 3
  pode demorar — vou monitorar e avisar se for ficar longo, sem matar o
  worker no meio de um item em processamento.
- `OLLAMA_VISION_MODEL=qwen2.5vl:7b` precisa existir no Ollama local para
  descrição de imagens; se não existir, imagens novas vão falhar na etapa de
  descrição (vídeos usam Whisper, não Ollama vision, e não são afetados).

## Status (2026-10-06, 21:20)
- `IG_SESSIONID` já está preenchido no `.env` (confirmado, conteúdo não lido).
- O servidor MCP `insta-kb` precisou ser reiniciado para ler o novo
  `IG_SESSIONID` (pydantic-settings só lê `.env` na inicialização). Esse
  servidor é gerenciado pelo próprio Claude Code via stdio (`.mcp.json`,
  comando `uv run python src/mcp_server/server.py`) — **não** é um processo
  solto no terminal do usuário.
- Ao matar o processo antigo para forçar reload, o pipe stdio do harness foi
  cortado; as ferramentas `mcp__insta-kb__*` ficaram indisponíveis nesta
  sessão e não reconectam sozinhas.
- Usuário vai fazer reload da sessão do Claude Code (reabrir) para o harness
  relançar o MCP server automaticamente com o `.env` atualizado.
- **Ao retomar**: pular a parte de reiniciar o MCP (já vai estar com o
  sessionid novo) e seguir direto do Passo 1 (`ig_sync_saved`). Nenhuma
  mudança de código foi feita até aqui, só config (`.env`, já editado pelo
  usuário) e processos.

## BUG encontrado (2026-10-06, 21:31) — bloqueia transcrição de TODO vídeo
**Sintoma**: todo item de vídeo falha na transcrição com:
```
TypeError: open() got an unexpected keyword argument 'metadata_errors'
```
em `src/infra/transcriber/transcriber.py:174` → `faster_whisper.transcribe.transcribe`
→ `faster_whisper/audio.py:decode_audio` → `av.open(input_file, mode="r",
metadata_errors="ignore")`.

**Causa raiz**: `pyproject.toml` fixa `av>=12.0.0` sem limite superior
(`uv.lock` resolveu para `av==19.0.1`, a mais recente). O PyPI `av` removeu o
parâmetro `metadata_errors` de `av.open()` em algum ponto entre `18.1.0` e
`19.0.0`. `faster-whisper` (última versão, `1.2.1`) ainda chama
`av.open(..., metadata_errors="ignore")` internamente e só declara
`av>=11` (sem upper bound) — ou seja, o próprio `faster-whisper` 1.2.1
está desatualizado em relação ao `av` mais novo; não há versão mais nova de
`faster-whisper` no PyPI que corrija isso (1.2.1 é a última).

**Testado nesta sessão** (via `uv run --with "av==X.Y.Z" python -c ...`,
chamando `av.open(..., metadata_errors="ignore")` e checando se dá
`TypeError` ou outro erro):
- `av==13.1.0` → aceita `metadata_errors` (OK)
- `av==16.0.0` → aceita `metadata_errors` (OK)
- `av==17.0.0` → aceita `metadata_errors` (OK)
- `av==19.0.1` (atual no lock) → `TypeError` (quebrado)
- `av==18.0.0`/`18.1.0` exigem Python>=3.11 (o ambiente do projeto está em
  3.10.20 — nem chegaram a instalar)
- `av==14.4.0` falhou por build (sem wheel pra essa plataforma/versão de
  Python, não é sinal de incompatibilidade de API)

**Correção recomendada (ainda não aplicada)**: fixar uma versão de `av`
testada como compatível, ex. `av>=12.0.0,<18` (ou pin exato `av==17.0.0`,
que foi a mais recente confirmada OK) em `pyproject.toml`, depois
`uv lock && uv sync`. Alternativa mais robusta a longo prazo: trocar a
chamada em `transcriber.py` para não depender do kwarg `metadata_errors` do
`av.open` (ex. deixar o `faster-whisper` decidir, ou fazer o decode de
áudio sem passar por essa API instável) — mas isso é código de terceiros
(`faster_whisper/audio.py`), não do nosso `transcriber.py`, então o pin de
versão é o caminho mais direto.

**Impacto no teste em andamento**: todo item de vídeo que passou pelo
worker falhou na transcrição (confirmado: `document_id=3712` foi o único
vídeo ingerido com sucesso — ele rodou *antes* desse erro aparecer; preciso
confirmar se 3712 realmente tinha transcrição ou se era um tipo de mídia
que não passa pelo Whisper). As falhas foram re-enfileiradas (retry,
`IG_MAX_ATTEMPTS=3`) e ainda não foram para a DLQ (`dead: 0` no momento da
pausa). Imagens (`describe` via Ollama vision) não são afetadas — só vídeos
(`transcribe` via faster-whisper/av).

**Estado ao pausar**: worker parado (`ig_worker_stop`, `consumers: 0`),
fila com `ready: 209` (a sincronização `ig_sync_saved` continuou rodando em
background e publicando mais itens — ela só enfileira, não consome, então
foi seguro deixá-la terminar). Processo do worker (`uv run python -m
workers.ig_worker`, background, log em
`/tmp/claude-1000/.../scratchpad/ig_worker.log`) ainda está de pé, só
pausado via comando de controle — não foi matado, para não perder o
estado de conexão com RabbitMQ.

**Próximos passos ao retomar**: usuário vai atualizar a ferramenta (fixar
versão do `av` no `pyproject.toml`, `uv lock`, `uv sync`, possivelmente
reinstalar o worker). Depois: `ig_worker_start` (ou relançar o processo) e
voltar a acompanhar a fila a partir de `ready: 209`.

## Verificação final
- `ig_queue_status` com `ready=0, dead=0` ao final.
- `kb_list_documents` com `total` maior que 3403 (se havia itens novos).
- `pytest tests/` passando (ou falhas reportadas e explicadas).
- Respostas 200 dos endpoints da API testados.
- Export gerado em `output/kb-export/` com sucesso via MCP.
