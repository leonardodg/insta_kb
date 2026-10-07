# Design — Task 10 (nova): Auditoria SOLID dos dois projetos

- **Data:** 2026-10-07
- **Status:** aprovado pelo usuário (brainstorming → design → ok)
- **Contexto:** plano mestre `~/.claude/plans/vamos-atualizar-a-lista-groovy-sun.md`
  (execução inline, branch `update/deps-2026-10` nos 2 repos, PR no fim).
  Usuário pediu uma etapa para verificar se os projetos precisam de
  refatoração pelo princípio SOLID.

## Decisões (brainstorming)

| Pergunta | Decisão |
|---|---|
| Escopo | **Só avaliar** — relatório go/no-go + backlog. Nenhum código muda. |
| Posição | **Task 10, no fim** (após Task 9) — decisão final do usuário; **sem renumeração** das Tasks 5–9 (a hipótese inicial de virar nova Task 5 foi descartada). |
| Profundidade | **Profunda**: checklist SOLID por princípio + ferramentas de métricas + análise de acoplamento. |
| Entregável | `docs/SOLID_AUDIT.md` em cada repo (estrutura idêntica) + seção    resumo/ponteiro no HANDOFF de cada + linha 10 da tabela. |

## Método (5 steps da nova Task 5)

1. **Pré-check de ferramentas** — `uvx radon` (cc + mi), `uvx vulture`,
   grafo de imports/ciclos via script AST próprio descartável (~50 linhas).
   One-off: **zero mudança em `pyproject.toml`/`uv.lock`**. Baseline de
   testes citada no relatório (168 passed insta_kb / 9 passed minimax),
   não re-executada (análise estática, sem GPU).
2. **Auditoria S/O** — coesão e funções multi-responsabilidade nos módulos
   grandes (`insta_kb`: `workers/ig_worker.py` 1031, `infra/llm/client.py`
   834, `core/knowledge/knowledge.py` 732; `minimax`:
   `minimax_mcp/server.py` 433, `orchestrator.py` 341). Open/Closed em
   cadeias if/elif sobre tipos (seleção de workflow, transports) e
   extensibilidade dos MCP tools.
3. **Auditoria L/I/D** — Liskov nas hierarquias reais (transports/fastmcp,
   device paths do transcriber; registrar N/A se couber); Interface
   Segregation nos clientes monolíticos (`llm/client`, `comfyui_client`) e
   contagem de tools por serviço; Dependency Inversion via grafo de
   imports (camada alta importando infra direto: ig_worker →
   instagrapi/db/ollama), ciclos, tangle.
4. **Síntese** — findings classificados **crítico / importante / menor /
   cosmético** (cosméticos só em apêndice, mitiga o ruído da abordagem
   princípio-a-princípio); backlog final **severidade × esforço**;
   veredito **go/no-go por repo**.
5. **Entregável** — `docs/SOLID_AUDIT.md` ×2 (estrutura idêntica), seção
   resumo no HANDOFF de cada repo, linha 10 da tabela de status.

## Impacto da posição (Task 10, sem renumeração)

- Plano mestre + 3 cópias versionadas (`docs/PLANO_ATUALIZACAO.md` nos 2
  repos, sempre md5-idênticas): inserir o corpo da Task 10 após a Task 9,
  nova linha `| 10 |` na tabela de status, "10 tasks" → "11 tasks".
  **Nenhuma renumeração** — referências cruzadas ("Task 5" = MCPs etc.)
  e o texto das sessões paralelas permanecem válidos.
- HANDOFFs dos 2 repos: ganham linha `| 10 |` na tabela.
- Ordem de execução livre: análise estática, não usa GPU, não briga com
  as Tasks 4/6/7 em andamento.

## Guardrails

- Nenhum arquivo de código alterado; deps só via `uvx` (nunca no lock).
- Relatório sem paths `/home/<user>` (repo minimax é público;
  `tests/unit_privacy.py` derruba a suíte).
- Commits separados por repo na branch `update/deps-2026-10`.
- Fora de escopo: executar refatorações (task/ciclo futuros se o
  veredito for go), mudanças de comportamento, re-executar testes.

## Critérios de aceite

1. `docs/SOLID_AUDIT.md` existe nos 2 repos com os 5 blocos
   (métricas S/O, L/I/D, acoplamento, backlog priorizado, veredito).
2. HANDOFF de cada repo tem seção resumo + linha 10 da tabela.
3. Plano mestre + 3 cópias com Task 10 adicionada (sem renumeração),
   md5-idênticos.
4. `git status` limpo de código; suítes inalteradas (nenhuma execução).
