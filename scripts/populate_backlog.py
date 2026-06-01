"""Popula o AIOS Backlog com itens reais dos projetos Climate, GRC Flow e Expansao AIOS."""

import sys, os
sys.stdout.reconfigure(encoding="utf-8")
os.chdir("C:/projetos/expansaoaios")

import httpx
from orchestrator.settings import settings

headers = {
    "Authorization": f"Bearer {settings.notion_api_key}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
http = httpx.Client(base_url="https://api.notion.com/v1", headers=headers, timeout=30)

backlog_items = [
    # ── CLIMATE ───────────────────────────────────────────────────────────────
    {
        "title": "CLIMA-043 – Alertas Slack: falhas de workflow, alertas criticos e operacionais",
        "project": "Climate", "status": "In Progress", "priority": "Critical",
        "description": "Notificacoes automaticas no Slack para falhas de coleta, alertas ONI/SST e eventos operacionais criticos.",
        "tags": ["alertas", "slack", "infra"],
    },
    {
        "title": "CLIMA-041 – Posts automaticos diarios + alertas nas redes sociais",
        "project": "Climate", "status": "Ready", "priority": "High",
        "description": "Publicacao automatica de resumo climatico diario no LinkedIn e Threads via agente de marketing.",
        "tags": ["marketing", "automacao"],
    },
    {
        "title": "CLIMA-042 – Analytics e monitoramento de acessos (GA4 + eventos customizados)",
        "project": "Climate", "status": "Ready", "priority": "High",
        "description": "Integracao GA4 com eventos customizados para monitorar uso do dashboard e engajamento.",
        "tags": ["analytics", "frontend"],
    },
    {
        "title": "CLIMA-039 – Preparacao para lancamento publico",
        "project": "Climate", "status": "Backlog", "priority": "High",
        "description": "Checklist pre-lancamento: performance, SEO, onboarding, documentacao publica.",
        "tags": ["lancamento", "produto"],
    },
    {
        "title": "CLIMA-040 – Lancamento Publico (meta: 20/06/2026)",
        "project": "Climate", "status": "Backlog", "priority": "Critical",
        "description": "Go-live do Expansao AI Climate para o publico geral.",
        "tags": ["lancamento", "milestone"],
    },
    {
        "title": "CLIMA-034 – Analise Preditiva: card interativo no dashboard",
        "project": "Climate", "status": "Backlog", "priority": "High",
        "description": "Card de analise preditiva climatica com IA local, exibindo probabilidades e tendencias.",
        "tags": ["ia", "dashboard", "preditivo"],
    },
    {
        "title": "CLIMA-044 – Painel de Observability da infra Expansao AI",
        "project": "Climate", "status": "Backlog", "priority": "Medium",
        "description": "Dashboard de monitoramento de toda a infraestrutura (collectors, API, workflows N8N).",
        "tags": ["infra", "observability"],
    },
    # ── GRC FLOW ──────────────────────────────────────────────────────────────
    {
        "title": "GRC-001 – Modulo Base de Conhecimento: CRUD de artigos e procedimentos",
        "project": "GRC Flow", "status": "In Progress", "priority": "Critical",
        "description": "Criacao e gestao de artigos, procedimentos e politicas com versionamento automatico.",
        "tags": ["backend", "base-conhecimento"],
    },
    {
        "title": "GRC-002 – Modulo BPM: fluxos de aprovacao e workflows",
        "project": "GRC Flow", "status": "Ready", "priority": "High",
        "description": "Gestao de processos com aprovacoes, notificacoes e rastreabilidade de conformidade.",
        "tags": ["backend", "bpm", "compliance"],
    },
    {
        "title": "GRC-003 – Deploy modulos Cadastro e Matching em producao (AEGEA)",
        "project": "GRC Flow", "status": "In Progress", "priority": "Critical",
        "description": "Subir modulos de cadastro e matching para producao. Credenciais solicitadas ao Anderson (prefeitura). Meta: esta semana.",
        "tags": ["devops", "aegea", "producao"],
    },
    {
        "title": "GRC-004 – Modulo Tributario: finalizacao e homologacao (AEGEA)",
        "project": "GRC Flow", "status": "In Progress", "priority": "High",
        "description": "Finalizar desenvolvimento do modulo tributario para subir em homologacao na proxima semana.",
        "tags": ["backend", "tributario", "aegea"],
    },
    {
        "title": "GRC-005 – Definicao de rubricas taxa lixo e juros/multa (AEGEA)",
        "project": "GRC Flow", "status": "Backlog", "priority": "High",
        "description": "Lana e Francine devem definir rubricas no sistema de producao. Marcos formalizou por e-mail.",
        "tags": ["aegea", "dados", "prerequisito"],
    },
    # ── EXPANSAO AI OS ────────────────────────────────────────────────────────
    {
        "title": "AIOS-001 – Configurar canais Slack (#cwi-aios e #expansao-aios)",
        "project": "Expansao AIOS", "status": "Backlog", "priority": "High",
        "description": "Criar canais, gerar webhooks e adicionar ao .env e variaveis Coolify para ativar notificacoes.",
        "tags": ["infra", "slack", "notificacoes"],
    },
    {
        "title": "AIOS-002 – Testar Agile Coach Agent com metricas reais da CWI",
        "project": "Expansao AIOS", "status": "Backlog", "priority": "Medium",
        "description": "Coletar metricas do time CWI (velocidade, cycle time) e rodar primeiro Agile Coach report.",
        "tags": ["cwi", "agile", "teste"],
    },
    {
        "title": "AIOS-003 – Ativar Executive Reporting Agent (relatorio diretoria CWI)",
        "project": "Expansao AIOS", "status": "Backlog", "priority": "Medium",
        "description": "Consolidar PMO + Agile Coach em relatorio executivo para diretoria CWI.",
        "tags": ["cwi", "executive", "relatorio"],
    },
    {
        "title": "AIOS-004 – Atualizar env vars Coolify (N8N + Coolify credentials)",
        "project": "Expansao AIOS", "status": "Backlog", "priority": "High",
        "description": "Adicionar N8N_BASE_URL, N8N_API_KEY, COOLIFY_BASE_URL, COOLIFY_API_KEY no servidor para DevOps Agent funcionar.",
        "tags": ["infra", "devops", "coolify"],
    },
]

ok = 0
for item in backlog_items:
    try:
        r = http.post("/pages", json={
            "parent": {"database_id": settings.notion_backlog_db_id},
            "properties": {
                "Name":        {"title": [{"text": {"content": item["title"]}}]},
                "Project":     {"select": {"name": item["project"]}},
                "Status":      {"select": {"name": item["status"]}},
                "Priority":    {"select": {"name": item["priority"]}},
                "Description": {"rich_text": [{"text": {"content": item["description"]}}]},
                "Tags":        {"multi_select": [{"name": t} for t in item.get("tags", [])]},
            },
        })
        r.raise_for_status()
        ok += 1
        print(f"  OK: {item['title'][:65]}")
    except Exception as e:
        print(f"  FAIL: {item['title'][:50]} — {e}")

print(f"\n{ok}/{len(backlog_items)} itens adicionados ao AIOS Backlog")
