"""Substitui as stories fabricadas pelo PM Agent pelas stories reais do Notion."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.chdir(os.path.dirname(os.path.dirname(__file__)))

from tools.run_tracker import upsert_story, _conn

SPRINT = "2026-W23"

# Stories reais conforme Notion — fonte única de verdade
REAL_STORIES = [
    # CLIMA-043 — Alertas Slack (4 stories reais)
    ("US-004", "climate", "CLIMA-043", "Alertas Slack — falhas e operacional",
     "Módulo centralizado slack_notifier.py"),
    ("US-005", "climate", "CLIMA-043", "Alertas Slack — falhas e operacional",
     "Alertas de falha de workflow N8N em #cwi-aios"),
    ("US-006", "climate", "CLIMA-043", "Alertas Slack — falhas e operacional",
     "Alertas de threshold climático com deduplicação"),
    ("US-007", "climate", "CLIMA-043", "Alertas Slack — falhas e operacional",
     "Alertas operacionais (DB, cache miss, invalidação pós-coleta)"),

    # CLIMA-042 — Analytics GA4 (4 stories reais)
    ("US-008", "climate", "CLIMA-042", "Analytics e monitoramento GA4",
     "Configuração base GA4 com pageview automático"),
    ("US-009", "climate", "CLIMA-042", "Analytics e monitoramento GA4",
     "Eventos climate_search e climate_data_viewed (client + server-side)"),
    ("US-010", "climate", "CLIMA-042", "Analytics e monitoramento GA4",
     "Funil de onboarding instrumentado com funnel_session_id"),
    ("US-011", "climate", "CLIMA-042", "Analytics e monitoramento GA4",
     "Eventos de alertas, erros, compartilhamento e latência"),

    # CLIMA-039 — Preparação lançamento (4 stories reais)
    ("US-012", "climate", "CLIMA-039", "Preparação para lançamento público",
     "Publicar checklist de lançamento no Notion com donos e deadlines"),
    ("US-013", "climate", "CLIMA-039", "Preparação para lançamento público",
     "Auditoria e correção de performance e SEO"),
    ("US-014", "climate", "CLIMA-039", "Preparação para lançamento público",
     "Health check, headers de segurança e rate limiting"),
    ("US-015", "climate", "CLIMA-039", "Preparação para lançamento público",
     "Smoke test final e autorização de go-live pelo CEO Agent"),

    # AIOS-001 — Slack infra (mantidas, projeto correto)
    ("US-AIOS-001", "aios", "AIOS-001", "Configurar canais Slack",
     "Criar canais Slack #cwi-aios e #expansao-aios"),
    ("US-AIOS-002", "aios", "AIOS-001", "Configurar canais Slack",
     "Smoke test de conectividade Slack"),
    ("US-AIOS-003", "aios", "AIOS-004", "Atualizar env vars Coolify",
     "Configurar e validar credenciais Coolify e N8N"),
]

# 1. Apagar stories fabricadas do W23
c = _conn()
with c.cursor() as cur:
    cur.execute("DELETE FROM pipeline_stories WHERE sprint = %s", (SPRINT,))
    deleted = cur.rowcount
c.commit()
c.close()
print(f"Removidas {deleted} stories fabricadas do W23")

# 2. Inserir stories reais
print(f"\nInserindo {len(REAL_STORIES)} stories reais:")
for story_id, project, epic_id, epic_title, title in REAL_STORIES:
    upsert_story(
        sprint=SPRINT,
        story_id=story_id,
        title=title,
        project=project,
        epic_id=epic_id,
        epic_title=epic_title,
        prd_title=epic_title,
        status="backlog",  # status real — sem código gerado ainda para estas
    )
    print(f"  {story_id:12} [{project:8}] {epic_id:12} — {title[:50]}")

# 3. Resumo final
c = _conn()
with c.cursor() as cur:
    cur.execute("""
        SELECT project, epic_id, count(*) as total
        FROM pipeline_stories WHERE sprint=%s
        GROUP BY project, epic_id ORDER BY project, epic_id
    """, (SPRINT,))
    print(f"\nResumo W23 corrigido:")
    for r in cur.fetchall():
        print(f"  {r[0]:10} {r[1]:12} → {r[2]} stories")
c.close()
