"""Corrige as stories do W23: remove AIOS/GRC contaminantes, adiciona epic_id correto."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.chdir(os.path.dirname(os.path.dirname(__file__)))

from tools.run_tracker import upsert_story, _conn

SPRINT = "2026-W23"

# Mapeamento correto: story_id → (project, epic_id, epic_title)
STORY_MAP = {
    # ── AIOS (não pertencem ao Climate — serão marcadas como aios) ────────────
    "US-001": ("aios",    "AIOS-001", "Configurar canais Slack"),
    "US-002": ("aios",    "AIOS-001", "Configurar canais Slack"),
    "US-014": ("aios",    "AIOS-004", "Atualizar env vars Coolify"),

    # ── CLIMA-043: Alertas Slack ───────────────────────────────────────────────
    "US-003": ("climate", "CLIMA-043", "Alertas Slack — falhas e operacional"),
    "US-004": ("climate", "CLIMA-043", "Alertas Slack — falhas e operacional"),
    "US-005": ("climate", "CLIMA-043", "Alertas Slack — falhas e operacional"),
    "US-006": ("climate", "CLIMA-043", "Alertas Slack — falhas e operacional"),
    "US-007": ("climate", "CLIMA-043", "Alertas Slack — falhas e operacional"),
    "US-008": ("climate", "CLIMA-043", "Alertas Slack — falhas e operacional"),
    "US-009": ("climate", "CLIMA-043", "Alertas Slack — falhas e operacional"),

    # ── CLIMA-042: Analytics GA4 ──────────────────────────────────────────────
    "US-010": ("climate", "CLIMA-042", "Analytics e monitoramento GA4"),
    "US-011": ("climate", "CLIMA-042", "Analytics e monitoramento GA4"),
    "US-012": ("climate", "CLIMA-042", "Analytics e monitoramento GA4"),
    "US-013": ("climate", "CLIMA-042", "Analytics e monitoramento GA4"),

    # ── CLIMA-039: Preparação para lançamento ─────────────────────────────────
    "US-015": ("climate", "CLIMA-039", "Preparação para lançamento público"),
    "US-016": ("climate", "CLIMA-039", "Preparação para lançamento público"),
    "US-017": ("climate", "CLIMA-039", "Preparação para lançamento público"),
    "US-018": ("climate", "CLIMA-039", "Preparação para lançamento público"),
    "US-019": ("climate", "CLIMA-039", "Preparação para lançamento público"),
    "US-020": ("climate", "CLIMA-039", "Preparação para lançamento público"),
}

# Carregar dados existentes do PM + Dev
with open("outputs/pm_prds_2026-W23.json", encoding="utf-8") as f:
    pm = json.load(f)
pm_stories = {
    s["id"]: {"title": s.get("title", ""), "prd_title": prd.get("title", "")}
    for prd in pm.get("prds", [])
    for s in prd.get("stories", [])
}

with open("outputs/dev_2026-W23.json", encoding="utf-8") as f:
    dev = json.load(f)
dev_files = {
    i["story_id"]: len(i.get("files_created", []))
    for i in dev.get("implementations", []) if i.get("story_id")
}

qa_results = {}
try:
    with open("outputs/qa_2026-W23.json", encoding="utf-8") as f:
        qa = json.load(f)
    reps = qa.get("reports", [qa]) if not qa.get("reports") and qa.get("story_id") else qa.get("reports", [])
    for r in reps:
        sid = r.get("story_id", "")
        if sid:
            rec = r.get("recommendation", "")
            issues = [i for i in r.get("code_quality", {}).get("issues", []) if i.get("severity") == "critical"]
            qa_results[sid] = {
                "result": "approved" if rec in ("deploy", "deploy_with_caveats") and not issues else "rejected" if issues else "reviewed",
                "notes": "; ".join(i.get("description", "") for i in issues[:2]),
            }
except FileNotFoundError:
    pass

print(f"{'Story':8} {'Project':8} {'Epic':12} {'Status':12} {'Arq':4} {'Título'}")
print("-" * 80)

for sid, (project, epic_id, epic_title) in STORY_MAP.items():
    info = pm_stories.get(sid, {})
    files = dev_files.get(sid, 0)
    qa = qa_results.get(sid, {})

    if qa.get("result") == "approved":
        status = "qa_approved"
    elif qa.get("result") == "rejected":
        status = "qa_rejected"
    elif qa.get("result") == "reviewed":
        status = "qa"
    elif files > 0:
        status = "dev"
    else:
        status = "backlog"

    upsert_story(
        sprint=SPRINT, story_id=sid,
        title=info.get("title", ""),
        project=project,
        epic_id=epic_id,
        epic_title=epic_title,
        prd_title=info.get("prd_title", ""),
        status=status,
        dev_files=files,
        qa_result=qa.get("result", ""),
        qa_notes=qa.get("notes", ""),
    )
    marker = "⚠ AIOS" if project == "aios" else "✅"
    print(f"{sid:8} {project:8} {epic_id:12} {status:12} {files:4}  {marker} {info.get('title','')[:40]}")

# Resumo
c = _conn()
with c.cursor() as cur:
    cur.execute("""
        SELECT project, epic_id, status, count(*)
        FROM pipeline_stories WHERE sprint=%s
        GROUP BY project, epic_id, status
        ORDER BY project, epic_id, status
    """, (SPRINT,))
    print("\nResumo por projeto/épico/status:")
    for r in cur.fetchall():
        print(f"  {r[0]:10} {r[1]:12} {r[2]:12} → {r[3]} stories")
c.close()
