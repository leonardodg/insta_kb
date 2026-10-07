# Auditoria SOLID — insta_kb

Data: 2026-10-07 · Método: radon cc/mi + vulture + grafo AST + revisão manual checklist · Baseline: 168 passed (não re-executado) · Escopo: só avaliar (nenhum código alterado)

---

## 1. Métricas

### 1.1 Coleta

| Ferramenta | Resultado |
|---|---|
| radon cc (JSON) | 170 funções medidas em 16 módulos; **38 com cc ≥ 6** (22%), 16 com cc ≥ 10, **8 com cc ≥ 15** |
| radon mi (JSON) | piores: `workers/ig_worker.py` = **19,29** · `core/knowledge/knowledge.py` = 32,64 · `infra/llm/client.py` = 44,45 · `infra/instagram/ig_sync.py` = 49,68 · `infra/db/repository.py` = 54,48 |
| vulture (conf 80) | **arquivo vazio = 0 dead code detectado** (com ressalvas — ver F8) |
| grafo de imports (AST) | 35 módulos, 40 arestas, **0 ciclos**; fan-out top: `workers.ig_worker`=9, `core.knowledge.knowledge`=7, `mcp_server.server`=5; fan-in top: `core.settings.config`=11 |
| Revisão manual | 35 arquivos / 5 337 linhas em `src/` lidos nos módulos que concentram risco (`ig_worker`, `knowledge`, `llm/client`, `ig_sync`, `queue`, `repository`, `vault`, `transcriber`, `downloader`, `mcp_server`, `api`, `app`, `config`) |

### 1.2 Piores funções (cc ≥ 13)

| cc | Posição | Função |
|---|---|---|
| 20 | `src/workers/ig_worker.py:293` | `_build_image_document` |
| 19 | `src/infra/vault/vault.py:26` | `write_markdown_copy` |
| 18 | `src/workers/ig_worker.py:373` | `process_message` |
| 18 | `src/workers/ig_worker.py:172` | `_build_video_document` |
| 17 | `src/core/knowledge/knowledge.py:340` | `_ingest_markdown_file` |
| 15 | `src/infra/llm/client.py:481` | `generate_structured` |
| 15 | `src/core/knowledge/knowledge.py:274` | `ingest_markdown` |
| 15 | `src/core/knowledge/knowledge.py:91` | `ingest_text` |
| 13 | `src/workers/ig_worker.py:573` | `_default_download` |
| 13 | `src/workers/ig_worker.py:516` | `_targets_from_info` |

### 1.3 Distribuição das 38 funções cc ≥ 6 por módulo

| módulo | cc ≥ 6 | total de funções | MI |
|---|---|---|---|
| `workers/ig_worker.py` | **11** | 28 | 19,29 |
| `core/knowledge/knowledge.py` | **9** | 15 | 32,64 |
| `infra/instagram/ig_sync.py` | 7 | 12 | 49,68 |
| `infra/llm/client.py` | 5 | 21 | 44,45 |
| `infra/transcriber/transcriber.py` | 3 | 7 | 66,77 |
| `infra/vault/vault.py` | 1 | 2 | 69,84 |
| `infra/downloader/downloader.py` | 1 | 7 | 70,70 |
| `infra/db/repository.py` | 1 | 14 | 54,48 |

**Os dois módulos `ig_worker` + `knowledge` concentram 20 das 38 funções cc ≥ 6 (53%).** Todos os outros 27 módulos/`__init__` estão em MI ≥ 62.

### 1.4 Nota de tooling (não é achado de código)

`except ValueError, TypeError:` sem parênteses (`src/workers/ig_worker.py:978`, `src/infra/llm/client.py:671`, `src/infra/queue/queue.py:133` e `:149`) é sintaxe **PEP 758 do Python 3.14** — o projeto declara `requires-python = ">=3.14"` (`pyproject.toml:8`), portanto é **válido aqui**, mas quebra radon/vulture rodados com Python < 3.14 (contornado na coleta via `uv run --with`). Registrado como limitação de ferramenta, não como defeito.

Observação sobre rank: o `mi_rank` do radon marca tudo como "A" (limiar de A é score > 19); `ig_worker.py` está em **19,29 — a 0,29 do rank B**. Use o valor numérico, não a letra (Apêndice C2).

---

## 2. Single Responsibility / Open-Closed

### 2.1 S — Single Responsibility

#### F1 · **Importante** · SRP — `src/workers/ig_worker.py` é um módulo de ~12 responsabilidades

1 031 linhas, 28 funções, MI 19,29, 11 das 38 funções cc ≥ 6. Ele é o orquestrador do daemon **e** o lugar onde mora cada etapa do pipeline. Responsabilidades identificadas, com posição:

| # | Responsabilidade | Evidência |
|---|---|---|
| 1 | Limpeza de texto / remoção de CTA (regex de vocabulário) | `strip_cta` `:96`, `clean_title` `:121`, `_CTA_PATTERNS` `:56-93` |
| 2 | Classificação de mídia por extensão | `classify_file` `:154-155` |
| 3 | Ritmo/throttling do consumo | `pace_sleep_seconds` `:158`, `_pace_after` `:858` |
| 4 | Montagem de documento de **vídeo** (fala+tela+descrição+legenda) | `_build_video_document` `:172-259` (cc=18) |
| 5 | Montagem de documento de **imagem/carrossel** | `_build_image_document` `:293-370` (cc=20), `_describe_one_image` `:262` |
| 6 | Orquestração download→transcrever→ingerir | `process_message` `:373-455` (cc=18) |
| 7 | Comandos de fila (start/stop, basic_consume/cancel) | `apply_command` `:458-488` (cc=10) |
| 8 | Cálculo de alvos de download do Instagram | `_targets_from_info` `:516-555` (cc=13), `_download_targets` `:491` |
| 9 | Download autenticado (instagrapi) + fallback yt-dlp + cache em disco | `_default_download` `:573-630` (cc=13), `existing_media` `:563` |
| 10 | Extração de quadros via ffmpeg/ffprobe (subprocess) | `extract_frames` `:636-692` |
| 11 | Fusão/deduplicação de texto de tela + capa | `merge_screen_text` `:695-717` (cc=11), `guardar_capa` `:720` |
| 12 | Leitura de tela via LLM de visão | `_default_read_screen` `:734-770` (cc=11) |
| 13 | Retenção/apagamento de mídia + vocabulário de categorias (global) | `_discard_media` `:776`, `_category_vocabulary` `:792-805` |
| 14 | Transcrição + trava de GPU | `_default_transcribe` `:813-837` |
| 15 | Persistência de progresso (state file) + retry/DLQ | `_record_progress` `:866`, `_requeue_or_dead_letter` `:895` |
| 16 | Consumidor AMQP + loop de reconexão do daemon | `_on_work` `:909-964`, `run` `:967-1027` |

**Evidência de que já é um tangle:** o módulo importa 5 infraestruturas no topo (`:47-52`: `core.knowledge`, `settings`, `infra.db`, `infra.instagram`, `infra.llm`, `infra.queue`) e mais 3 lazy (`:622` yt-dlp, `:817-818` gpu_lock/transcriber) — fan-out 9, o maior do repo. Cada uma das 16 linhas acima é uma razão independente para o arquivo mudar. O próprio docstring admite a ambiguidade de localização (`:5-12`).

**Atenuante real (não é desculpa, é desenho bom):** a lógica pura foi extraída para funções de topo testáveis — `process_message` recebe todos os IOs injetados como callables (`:376-380`), e 14 dos 28 testes de `test_workers_ig_worker.py` exercitam esse caminho sem RabbitMQ/GPU/rede. O problema é **concentração de módulo**, não função incontrolável.

#### F2 · **Importante** · SRP — `src/core/knowledge/knowledge.py` acumula 5 domínios

732 linhas, 15 funções, MI 32,64, 9 funções cc ≥ 6. O docstring (`:1-29`) se apresenta como "orchestration layer", mas na prática é um facadc de cinco mudanças independentes:

- **Ingestão de texto** (LLM → DB → vault): `ingest_text` `:91-178` (cc=15).
- **Pipelines de mídia** (download + transcrição + sniff de plataforma): `ingest_video` `:181-224`, `ingest_audio` `:227-271` — inclui a cadeia `if "instagram" in url / elif "youtu" in url` `:207-212`.
- **Importação markdown** (fs + frontmatter YAML + seção Summary + LLM): `ingest_markdown` `:274-337` (cc=15), `_ingest_markdown_file` `:340-404` (cc=17), `_save_document_with` `:407-468`.
- **RAG/consulta**: `search` `:471`, `ask` `:484-521` (montagem de prompt + citação de fontes), `reindex` `:524`.
- **Catálogo/exportação** (consulta SQL + validação de path anti-traversal + escrita de arquivos): `export_search` `:559-609` (cc=11), `list_documents` `:612`, `export_documents` `:670-732` (inclui regra de segurança `:698-701`).

Novamente: funções bem fatiadas e com testes, mas **5 motivos de mudança num arquivo só**. Ele também é o segundo maior fan-out do repo (7).

#### F7 · **Menor** · SRP — `src/app/main.py` é um stub sem responsabilidade real, mas é o entrypoint do pacote

`src/app/main.py:12-19` só imprime "Hello from app!" e o nome do settings. Ainda assim:

- `pyproject.toml:41` — `[project.scripts] app = "app:main"` ⇒ o **único console script instalado** do projeto é este stub;
- `pyproject.toml:2` — o pacote se chama `app`, enquanto o código real vive em `core/`, `infra/`, `workers/`, `api/`, `mcp_server/`;
- `pyproject.toml:96` — `known-first-party = ["app", "src"]` mantém vivo o resquício do scaffold.

Confirmado: nada em `src/`, `tests/` ou `README.md` usa `app.main` além do próprio pacote (o README roda `uvicorn api.main:app --app-dir src` e `python -m workers.ig_worker`).

#### F10 · **Menor** · SRP/DRY — o shape do documento é montado em 3 lugares

O dicionário `{"id", "title", "summary", "tutorial", "tags", ...}` que vai para `vault.write_markdown_copy` e para a resposta da API é escrito à mão em: `knowledge.py:152-162` (`ingest_text`), `knowledge.py:443-453` (`_save_document_with`) e `knowledge.py:540-556` (`_document_to_dict`). Três cópias que podem divergir (a de `ingest_text` **não** inclui `objectives`/`ig_pk`/`llm_model`, as outras incluem variações) — o vault silenciosamente escreve campo vazio em vez de falhar.

### 2.2 O — Open-Closed

#### F3 · **Importante** · OCP — seleção de provedor LLM é uma cadeia if/elif duplicada em 2 pontos

Não existe registry/estratégia; o provedor é escolhido por string comparison em **cada** função que fala com um modelo:

- `src/infra/llm/client.py:433-442` — dentro de `_generate_raw_with_retries`: `if provider == "ollama": ... elif provider == "openai-compatible": ... else: return {"ok": False, "error": "Unknown LLM_PROVIDER..."}`.
- `src/infra/llm/client.py:594-598` — dentro de `chat`: a mesma dupla `if/elif` com `raise RuntimeError(f"Unknown LLM_PROVIDER...")`.

Adicionar um terceiro provedor (ex.: outro endpoint OpenAI-compatible com auth diferente, ou um provedor remoto) exige editar **ambos** os ramos + os dois geradores `_ollama_generate` (`:263`) / `_openai_compatible_generate` (`:270`), que já são funções separadas mas não registráveis. 4 pontos de edição para 1 extensão = fechado na prática.

**Extensões correlatas (mesma doença, outros pontos):**
- `knowledge.py:207-212` — plataforma derivada por substring da URL (`"instagram" in url / "youtu" in url / else`), uma cadeia que cresce por host;
- pontuação de `_generate_raw_with_retries` cc=9 e `chat` cc=5 só por causa desses ramos.

#### F6 · **Menor** · OCP/SRP — a taxonomia de tipo de mídia vive em 4 pontos que precisam mudar juntos

Hoje são 2 tipos e o dispatch já é parcialmente polimórfico (a escolha vídeo/imagem em `process_message:399-407` só encaminha para `_build_video_document`/`_build_image_document`). Mas a *classificação* está espalhada:

1. `src/workers/ig_worker.py:154-155` — `classify_file` decide por **extensão** (`VIDEO_EXTS`);
2. `src/workers/ig_worker.py:511-555` — `_targets_from_info` decide por **inteiro instagrapi** (`1/2/8`, constantes reapontadas em `:508-513` com comentário explicando que `MEDIA_TYPES` de `ig_sync` foi "repeated here (not imported)");
3. `src/infra/instagram/ig_sync.py:39` — `MEDIA_TYPES = {1: "image", 2: "video", 8: "carousel"}`;
4. ramos soltos `if kind == "video"` / `== "image"` em `ig_worker.py:400` e `:736`, e `doc_type == "image"` em `knowledge.py:119`.

Duas taxonomias paralelas (extensão vs. `media_type`) que precisam concordar sem que nada as garanta. Um tipo novo (ex.: post de áudio) toca todos os 4.

---

## 3. Liskov / Interface Segregation / Dependency Inversion

### 3.1 L — Liskov Substitution

**Hierarquia real no repo: exatamente uma.**

- `TolerantClient(Client)` — `src/infra/instagram/ig_sync.py:171-229`: subclasse do `Client` do instagrapi criada em runtime que sobrescreve **só** `collection_medias_v1_chunk`, mantendo assinatura e sem afrouxar o contrato: erros de requisição (429, sessão morta) continuam subindo (`:205-208`), apenas item malformado é tolerado (`:222-227`). **LSP respeitada** — a substituição é observável só no sentido desejado.

**N/A no resto do projeto** — e isso é o achado: não há hierarquias de domínio. O que existe é duck-typing informal:

- `hasattr(client, "collection_medias")` (`ig_sync.py:275`) para detectar API legada;
- parâmetros anotados `client: Any` (`ig_sync.py:247`, `:389`), `info: Any` (`ig_worker.py:516`);
- callables injetados (`Callable[[str], dict[str, Any]]`) como substitutos de "transcriber"/"describer" (`ig_worker.py:376-380`).

Sem hierarquia formal não há substituição a violar, **mas também não há contrato que o type checker possa verificar**: um substituto que devolva `{"ok": True}` sem a chave `"text"` explode em `tr["text"]` (`ig_worker.py:201`) em runtime, não em `pyright`. Isso vira F4.

### 3.2 I — Interface Segregation

#### F4 · **Importante** · ISP + ausência de contratos — 0 `Protocol`/`ABC` no repo inteiro; o contrato universal é `dict[str, Any]`

Verificado por grep em `src/`: **nenhuma** ocorrência de `Protocol`, `ABC`, `abstractmethod` ou `runtime_checkable`. Em contrapartida:

**(a) Um único tipo de retorno para tudo.** Cada função devolve `{"ok": bool, ...}` e o chamador indexa por string: `built["text"]` / `built["categoria"]` (`ig_worker.py:411-416`), `res["status"]` (`:949`), `dl.get("filepaths")` (`:395`), `gen["resumo"]` (`knowledge.py:138`). A checagem de contrato é manual e em runtime.

**(b) Parâmetros gordos obrigaram a desligar o lint.** `pyproject.toml:91` — `ignore = ["PLR0913"]  # Permite mais de 5 argumentos em funções`. Contagem real:

| Função | Params | Posição |
|---|---|---|
| `db.save_document` | **16** | `src/infra/db/repository.py:92-111` |
| `knowledge._save_document_with` | **14** | `src/core/knowledge/knowledge.py:407-422` |
| `knowledge.ingest_text` | **10** | `src/core/knowledge/knowledge.py:91-103` |
| `ig_worker.process_message` | **7** (5 deles callables de IO) | `src/workers/ig_worker.py:373-382` |
| `knowledge.ingest_video` / `ingest_audio` | 6 cada | `knowledge.py:181-188`, `:227-234` |

`save_document` com 16 parâmetros é um "parâmetro por coluna" — sinal clássico de que falta um objeto de valor (`DocumentDraft`/`IngestPayload`).

**(c) `infra/llm/client.py` — 834 linhas, 21 funções de módulo, ≥ 6 contratos distintos sob um nome só:**

| Contrato | Funções |
|---|---|
| Transporte de chat (Ollama / OpenAI-compatível) | `_ollama_payload` `:245`, `_ollama_generate` `:263`, `_openai_compatible_generate` `:270` |
| Geração estruturada + retry de schema | `_generate_raw_with_retries` `:397` (cc=9), `generate_structured` `:481` (cc=15), `parse_llm_json` `:194` |
| Montagem de prompts | `SUMMARY_PROMPT_TEMPLATE` `:80`, `build_summary_prompt` `:128`, `build_vision_prompt` `:604`, `SCREEN_PROMPT` `:781` |
| Orçamento de contexto / corte | `orcamento_de_material` `:161`, `cortar_material` `:171` |
| Verificação de citação (ancoragem) | `ancorar_codigo` `:330` |
| Embeddings | `embed` `:577` |
| Visão: descrever / ler tela / b64 / coerência de categoria | `describe_image` `:740`, `read_screen` `:790`, `imagem_para_b64` `:690`, `parse_vision_reply` `:663`, `coerce_categoria` `:633` |

Quem só quer `embed` (`knowledge.py:476`) ou `chat` (`knowledge.py:509`) importa um módulo que também carrega prompts de visão, normalização PIL e regra de ancoragem de código. MI 44,45 é aceitável, mas o custo é de **acoplamento de contrato**, não de tamanho: nenhum ponto injeta um "LlmClient" — todos chamam o módulo concreto.

**(d) MCP tools: ISP bom, com 2 excessões.** Das 15 tools (`mcp_server/server.py`), **13 são delegates de 1 linha** para `knowledge.*` (`:203-357`) — contratos finos e corretos. Excessões:

- `ig_sync_saved` `:60-112` — 53 linhas fazendo conexão RabbitMQ + sessão Postgres + client Instagram + callback de progresso dentro da tool;
- `ig_get_progress` `:159-182` — lê state file **e** consulta `db.list_ig_pks` na mesma função (dois Eixos distintos).

### 3.3 D — Dependency Inversion

**Não existe inversão formal (0 abstrações), mas existe injeção funcional bem usada.** O padrão do repo é `Callable` injetado no lugar de interface:

| Ponto de injeção | Onde |
|---|---|
| `embed_fn: Callable[[str], list[float]]` em `save_document`/`search_documents`/`reindex_all` | `repository.py:108`, `:186`, `:243` — o repositório **não** importa o LLM (bom D) |
| `publish_fn` / `progress_fn` | `ig_sync.py:392-393` |
| `extract` / `on_discard` | `ig_sync.py:117-118` |
| `download`/`transcribe`/`describe`/`read_screen`/`ingest` callables | `ig_worker.py:376-380` — é o que permite testar o pipeline sem GPU/rede |
| `held()` context manager (trava de GPU) | `ig_worker.py:817-825`, consumido via `api/main.py:277-295` |
| `core.settings.config.settings` (singleton pydantic) | **fan-in 11** — o ponto de configuração mais reusado do repo, centralizado em `config.py:16-109` |

#### F11 · **Menor** · DIP — camada `core` e `workers` importam infraestrutura concreta, sem porta

- `src/core/knowledge/knowledge.py:42-43` — `from infra import db, vault` + `from infra.llm import client as llm`. A camada `core` (que o próprio layout nomeia como de alto nível) depende dos três concretos; trocar persistence/LLM/vault significa editar `knowledge.py`, não implementar uma porta.
- `src/workers/ig_worker.py:47-52` — imports no topo de `db`, `ig_sync`, `llm`, `queue` (+ lazy de `VideoDownloader` `:622`, `gpu_lock`/`transcriber` `:817-818`).
- `src/mcp_server/server.py:40-44` e `src/api/main.py:26-28` idem.

**Mitigantes que já existem e funcionam:** (1) os imports lazy documentados como PLC0415 reduzem o custo de importação e o acoplamento estático (`knowledge.py:194-195`, `:239`, `:242`; `ig_worker.py:622`, `:817-818`); (2) a injeção de callables da seção anterior — a *infraestrutura de IO* já é substituível nos testes; o que falta é uma fronteira nomeada (Protocol) para formalizar o que hoje é convenção.

#### F5 · **Importante** · Acoplamento de camada — `api/main.py` depende do `mcp_server` (outra camada de apresentação)

- `src/api/main.py:28` — `from mcp_server import server as mcp_server`;
- 9 endpoints delegam para ele: `:116`, `:130`, `:142`, `:153`, `:172`, `:186`, `:216`, `:236`, `:328`;
- `src/api/main.py:172` chama `mcp_server.knowledge.search(...)` — `mcp_server.knowledge` **não é um módulo**: resolve-se porque `server.py:40` faz `from core.knowledge import knowledge` e o atributo é re-exportado por acidente de nome. Renomear aquele import no `server.py` quebra a REST API em outro arquivo, silenciosamente.

A intenção (não duplicar lógica entre REST e MCP — `api/main.py:1-17`) é correta e vale manter; o que está invertido é a direção: **ambas** as apresentações deveriam depender de `core`/camada de aplicação, e não uma da outra. Efeito colateral concreto: subir só a REST API puxa `fastmcp`.

---

## 4. Acoplamento e coesão

### 4.1 Grafo (35 módulos, 40 arestas, 0 ciclos)

- **0 ciclos de import** — o único ponto onde posso dizer "não há dívida de ciclagem".
- **Fan-out (acoplamento de saída):** `workers.ig_worker`=9 · `core.knowledge.knowledge`=7 · `mcp_server.server`=5 · `api.main`=3 · `app.main`=2. Os dois primeiros são exatamente F1 e F2.
- **Fan-in (reuso):** `core.settings.config`=11 (bom — configuração centralizada), `infra`=4, `infra.downloader`=3, `infra.transcriber`=3. Nenhum módulo de negócio é "hub" além de config, o que é saudável.
- **Direção das camadas:** `api → mcp_server → core → infra` (acíclico) **exceto** F5 (`api → mcp_server`).

### 4.2 Tangle / coesão por módulo (MI)

| Módulo | MI | Leitura |
|---|---|---|
| `workers/ig_worker.py` | **19,29** | Tangle. 16 responsabilidades (F1), 11 funções cc ≥ 6, fan-out 9. É o único módulo no limiar do rank B do radon. |
| `core/knowledge/knowledge.py` | 32,64 | Facade de 5 domínios (F2), 9 funções cc ≥ 6, fan-out 7. |
| `infra/llm/client.py` | 44,45 | Coeso em "falar com LLM", mas com 6 contratos empilhados (F4c). |
| `infra/instagram/ig_sync.py` | 49,68 | Razoavelmente coeso; 7 funções cc ≥ 6 vêm de tratamento tolerante a falha — justificado e comentado. |
| `infra/db/repository.py` | 54,48 | Coeso; o `save_document` de 16 params é o único desvio (F4b). |
| Demais (vault, queue, transcriber, downloader, gpu_lock, config, api, mcp) | 62–100 | Saudáveis. |

### 4.3 Acoplamento residual (menores)

- `mcp_server.server` mistura apresentação com wiring de infraestrutura na tool `ig_sync_saved` (`server.py:80-112`) e leitura de arquivo + query em `ig_get_progress` (`:166-182`) — **F12 · Menor**.
- Config congelada em import-time: `ig_worker.py:139-149` copia `settings.*` para constantes de módulo; `llm/client.py:27-42` idem. Mudança de config depois do import não tem efeito, e o teste precisa monkeypatchar o módulo, não o `settings` — **F9 · Menor**.
- Os valores de contrato de `RABBITMQ_URL`/`QUEUE`/`MAX_ATTEMPTS` são lidos no import de `queue.py:33-37` — mesmo padrão, mesma leitura.

---

## 5. Backlog priorizado (severidade × esforço)

Esforço: **S** = ≤ ½ dia · **M** = 1–3 dias · **L** = > 3 dias (com a suíte de 168 testes como rede de segurança).

| # | Sev. | Esf. | Finding | Onde | Ação sugerida |
|---|---|---|---|---|---|
| 1 | Importante | **S** | F3 — provedor LLM por if/elif duplicado | `infra/llm/client.py:433-442`, `:594-598` | Registry `PROVIDERS = {"ollama": fn, "openai-compatible": fn}`; as duas cadeias passam a resolver pelo mesmo dicionário. 1 ponto de extensão. |
| 2 | Importante | **S** | F5 — `api` depende de `mcp_server` + re-export frágil | `api/main.py:28,172,186,216,236` | Mover a delegação para um módulo de aplicação compartilhado (ou chamar `core.knowledge` direto); REST e MCP passam a importar `core`, não um ao outro. |
| 3 | Importante | **M** | F1 — `ig_worker.py` acumula 16 responsabilidades | `workers/ig_worker.py` (todo) | Fatiar em ~4 módulos mantendo `process_message` puro e a assinatura atual: `worker/text.py` (CTA/título), `worker/media_download.py` (instagrapi+yt-dlp+disco), `worker/screen.py` (ffmpeg+OCR+merge), `worker/consumer.py` (AMQP/estado/ritmo). `ig_worker.py` vira só orquestração. |
| 4 | Importante | **M** | F2 — `knowledge.py` com 5 domínios | `core/knowledge/knowledge.py` | Separar `ingest.py` (texto/mídia/markdown), `query.py` (search/ask/reindex), `export.py` (catálogo/export+validação de path). Mantém `knowledge` como fachada de compatibilidade para as 13 tools/endpoints. |
| 5 | Importante | **M** | F4 — 0 Protocol/`dict[str,Any]`/params gordos | repo inteiro; `repository.py:92`, `knowledge.py:91,407` | (a) `TypedDict`/Pydantic para os retornos `{"ok": ...}` — elimina índice-string; (b) `Protocol` `LlmClient`/`Embedder` nos pontos já injetados; (c) `save_document(session, draft: DocumentDraft, embed_fn)` derruba 16 params para ~4. Habilitar PLR0913 de novo ao final. |
| 6 | Menor | **S** | F7 — stub `app/` é o console script | `pyproject.toml:2,41,96`; `src/app/main.py` | Remover o script/stub (ou trocar `app = "app:main"` por entradas reais: worker/API/MCP). |
| 7 | Menor | **S** | F8 — código morto que o vulture não pegou | `ig_worker.py:491-505`, `ig_sync.py:247-259`, `transcriber.py:243-251`, `downloader.py:162-169` | Remover `_download_targets` (docstring admite "unused"), `saved_posts` (sem chamador), `transcribe_video`/`download_video` (só re-export/teste) — ou marcar como API pública de propósito. Ver Apêndice: por que o vulture achou 0. |
| 8 | Menor | **S** | F9 — config congelada no import | `ig_worker.py:139-149`, `llm/client.py:27-42`, `queue.py:33-37` | Ler `settings.X` dentro das funções (como já faz `api/main.py`) ou expor uma função `reload()`; deixa os testes menos frágeis. |
| 9 | Menor | **M** | F10 — `doc_dict` montado em 3 lugares | `knowledge.py:152,443,540` | Uma única `_document_to_dict` usada pelos três caminhos (hoje já existe uma; apontar as outras duas para ela). |
| 10 | Menor | **M** | F12 — wiring de infra dentro das MCP tools | `mcp_server/server.py:60-112`, `:159-182` | Extrair `ig_sync_saved`/`ig_get_progress` para camada de aplicação; tools ficam 15/15 delegates. |
| 11 | Menor | **M** | F6 — taxonomia de mídia em 4 pontos | `ig_worker.py:154,511`, `ig_sync.py:39` | Fonte única do tipo (enum/constantes compartilhadas) + dispatch por tabela; `classify_file` passa a devolver o mesmo vocabulário de `MEDIA_TYPES`. |
| 12 | Menor | **L** | F11 — `core`/`workers` → infra concreta sem porta | `knowledge.py:42-43`, `ig_worker.py:47-52` | Só depois de F4/F3: introduzir `Protocol` de portas (Storage/Llm/Queue) e mover a composição para um único ponto (composition root). Esforço alto, benefício incremental — não faz sentido isolado. |
| — | Cosmético | — | C1–C6 | ver Apêndice A | Quando mexer no arquivo em questão. |

**Ordenação:** primeiro os quick wins de severidade Importante (S), depois os dois fatiamentos de módulo (M) que destravam o resto, depois menores baratos, e só no fim o trabalho de arquitetura formal (L).

---

## 6. Veredito go/no-go

> ### **CICLO FUTURA**

**Justificativa:** não há nenhum achado **crítico** — 0 ciclos, 0 dead code real, 168 testes verdes, hierarquia única (TolerantClient) respeitando LSP, injeção de dependência já funcional nos pontos certos (embed_fn, callables do `process_message`, publish_fn, settings); nada aqui justifica parar a linha de features para refatorar *agora*. Mas a coesão dos monstros é um problema real e crescente: `ig_worker.py` (MI 19,29, 16 responsabilidades) e `knowledge.py` (MI 32,64, 5 domínios) concentram 53% de todas as funções com cc ≥ 6 e metade do fan-out do repo — endereçar os itens 1–5 do backlog no **próximo ciclo de refatoração planejado**, antes de adicionar novos tipos de mídia ou novos provedores ao pipeline, é a decisão calibrada: SOLID como ferramenta, não como religião.

---

## Apêndice A: achados cosméticos

| ID | Achado | Posição |
|---|---|---|
| C1 | Sintaxe PEP 758 (`except ValueError, TypeError:`) quebra radon/vulture com Python < 3.14 — válido no projeto (`requires-python >= 3.14`), contornado via `uv run --with`. **Achado de tooling.** | `workers/ig_worker.py:978`, `infra/llm/client.py:671`, `infra/queue/queue.py:133,149` |
| C2 | `radon.mi_rank` emite "A" para tudo (limiar A > 19); a letra mascara que `ig_worker.py` está a 0,29 do rank B — usar o valor numérico em qualquer gate de CI futura. | saída mi |
| C3 | `[tool.setuptools.package-find]` e `known-first-party = ["app", "src"]` são resquícios do scaffold setuptools; o backend real é `uv_build` e o pacote `app` não contém o código. | `pyproject.toml:57-58`, `:96` |
| C4 | Bloco `if __name__ == "__main__"` comentado. | `api/main.py:331-332` |
| C5 | Docstring da REST API cita `mcp_server.knowledge.*` como se fosse caminho de módulo — é re-export acidental (ver F5). | `api/main.py:4` |
| C6 | **Fora de escopo SOLID, registrado para não passar desperbido:** o default de `settings.RABBITMQ_URL` é template não interpolado (`"{RABBITMQ_SCHEMA}://..."`), bug latente já documentado no próprio módulo; nunca hit hoje porque `.env` sempre define a variável. | `core/settings/config.py:56`, documentado em `infra/queue/queue.py:12-18` |

## Apêndice B: por que o vulture achou 0

Duas classes de dead code real passaram batidas com `--min-confidence 80`:

1. **Colisão de nome:** `_download_targets` é "usado" porque `client.saved_posts()` (`ig_sync.py:279`) casa com o nome `saved_posts` — o vulture vê identificador, não chamada.
2. **Re-export:** `transcribe_video` só existe no `__all__` de `infra/transcriber/__init__.py` — o import no `__init__` conta como uso.

Conclusão de método: vulture 0 ≠ sem dead code; a confirmação manual (grep de chamador) é necessária e foi feita para os 4 casos do item 7 do backlog.

## Apêndice C: o que não foi avaliado

1. **Comportamento em runtime** — análise 100% estática. O worker real contra RabbitMQ/Instagram/Ollama/GPU não foi exercitado (e a suíte completa **não** foi re-executada, conforme instrução; baseline citada: 168 passed).
2. **`scripts/`** (7 scripts operacionais, ~875 linhas) ficou fora das métricas coletadas (o grafo/radon cobrem `src/`) e fora do escopo deste relatório.
3. **Robustez de LSP do `TolerantClient` contra versões futuras do instagrapi** — ele sobrescreve um método interno (`collection_medias_v1_chunk`); validei a leitura do código e a ausência de enfraquecimento de contrato, mas não a compatibilidade em runtime com versões diferentes da pinada.
4. **Desempenho/segurança em profundidade** (bandit/pip-audit/CI) — fora do escopo SOLID; os checks existem no repo e estão descritos em `docs/REVISAO_2026-10-07.md`.
5. **`minimax-video-factory`** — o spec original prevê relatório nos dois repos; este documento cobre **apenas** `insta_kb`, conforme o pedido desta sessão.
