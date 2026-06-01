# Expansão AI OS

Orquestrador multi-agente que coordena o ciclo completo de desenvolvimento de projetos da [Expansão AI](https://expansaoai.com.br) — do backlog ao deploy.

## Arquitetura

```
Notion Backlog
     │
     ▼
┌─────────┐    human gate    ┌────────┐    human gate    ┌───────────┐
│ CEO     │ ───────────────▶ │ PM     │ ───────────────▶ │ Architect │
│ Agent   │                  │ Agent  │                  │ Agent     │
└─────────┘                  └────────┘                  └───────────┘
                                                               │
                                                               ▼
┌───────────┐    human gate    ┌────────┐                ┌─────────┐
│ Marketing │ ◀─────────────── │ DevOps │ ◀───────────── │ Dev     │
│ Agent     │                  │ Agent  │   human gate   │ Agent   │
└───────────┘                  └────────┘   (qa→deploy)  └─────────┘
                                    ▲                         │
                                    │                         ▼
                                    └──────────────────── ┌────────┐
                                                          │ QA     │
                                                          │ Agent  │
                                                          └────────┘
```

Todos os agentes usam **Claude** como motor. O **Notion** é a fonte única de verdade.

## Agentes

| Agente | Modelo | Responsabilidade | Gate |
|--------|--------|-----------------|------|
| CEO | Opus 4.8 | Lê backlog, prioriza, gera plano semanal | CEO→PM (humano) |
| PM | Sonnet 4.6 | Escreve PRDs e User Stories | — |
| Architect | Sonnet 4.6 | Propõe stack e documenta arquitetura | — |
| Dev | Sonnet 4.6 | Gera e revisa código (integra Claude Code) | — |
| QA | Sonnet 4.6 | Valida funcionalidades e APIs | QA→Deploy (humano) |
| DevOps | Haiku 4.5 | GitHub, Coolify, Docker via N8N | — |
| Marketing | Haiku 4.5 | Gera conteúdo LinkedIn/Threads | Content→Publish (humano) |

## Setup

### 1. Instalar dependências

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. Configurar variáveis de ambiente

```bash
cp .env.example .env
# Edite .env com suas chaves
```

Variáveis obrigatórias:
- `ANTHROPIC_API_KEY` — chave da API Anthropic
- `NOTION_API_KEY` — chave de integração Notion
- `NOTION_BACKLOG_DB_ID` — ID do banco de dados de backlog
- `NOTION_PROJECTS_DB_ID` — ID do banco de projetos
- `NOTION_SPRINTS_DB_ID` — ID do banco de sprints

### 3. Configurar o Notion

Crie uma integração em [notion.so/my-integrations](https://www.notion.so/my-integrations) e compartilhe os três bancos de dados com ela.

**Schema esperado do Backlog DB:**

| Campo | Tipo |
|-------|------|
| Name | Title |
| Description | Rich Text |
| Status | Select: `Backlog`, `In Progress`, `Done` |
| Priority | Select: `High`, `Medium`, `Low` |
| Project | Select: `Climate`, `GRC Flow`, `Interno` |
| Effort | Number |
| Tags | Multi-select |

## Uso

```bash
# Rodar pipeline completo (CEO → PM → ... → Marketing)
aios run

# Rodar apenas o CEO Agent
aios run --agent ceo

# Passar contexto adicional
aios run --context "Foco em performance esta semana — lançamento dia 15"

# Ver últimos outputs
aios status
```

## Estrutura do projeto

```
expansao-ai-os/
├── agents/
│   ├── config.yaml          # Configuração de todos os agentes
│   └── prompts/             # System prompts em Markdown
│       ├── ceo.md
│       ├── pm.md
│       ├── architect.md
│       ├── dev.md
│       ├── devops.md
│       ├── qa.md
│       └── marketing.md
├── orchestrator/
│   ├── agents/              # Implementação de cada agente
│   │   ├── ceo_agent.py
│   │   └── pm_agent.py      # TODO
│   ├── base_agent.py        # Classe base compartilhada
│   ├── pipeline.py          # Wiring dos agentes + gates
│   ├── settings.py          # Pydantic Settings (via .env)
│   └── cli.py               # CLI `aios`
├── tools/
│   ├── notion.py            # Integração Notion (memória compartilhada)
│   └── slack.py             # Notificações Slack
├── outputs/                 # JSONs persistidos por cada agente
├── .env.example
├── pyproject.toml
└── README.md
```

## Princípios arquiteturais

1. **Human-in-the-loop obrigatório** nos gates CEO→PM e QA→Deploy
2. **Outputs persistidos** antes de acionar o próximo agente
3. **Notion como fonte única da verdade** — todos os agentes lêem e escrevem no Notion
4. **Cada agente é independente** — pode ser rodado isoladamente
5. **Modelos proporcionais ao custo** — Opus para CEO, Haiku para tarefas simples

## Roadmap

- [x] CEO Agent (leitura Notion + plano semanal)
- [x] PM Agent (PRDs + User Stories)
- [ ] Architect Agent
- [ ] Dev Agent (integração Claude Code)
- [ ] QA Agent
- [ ] DevOps Agent (N8N + Coolify)
- [ ] Marketing Agent
- [ ] FastAPI para orquestração remota
- [ ] Dashboard de status no Notion
