from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Anthropic
    anthropic_api_key: str

    # Usage tracking (optional — falls back to outputs/agent_runs.jsonl if unset)
    database_url: str = ""
    # Secret for POST /track — external projects must send X-AIOS-Key header
    track_api_key: str = ""

    # Notion
    notion_api_key: str
    notion_backlog_db_id: str
    notion_projects_db_id: str
    notion_sprints_db_id: str

    # Slack
    slack_webhook_url_cwi: str = ""
    slack_webhook_url_expansao: str = ""

    # Specs DB
    notion_specs_db_id: str = ""

    # N8N
    n8n_base_url: str = ""
    n8n_api_key: str = ""

    # Coolify
    coolify_base_url: str = ""
    coolify_api_key: str = ""

    # N8N workflow IDs (pre-configured)
    n8n_deploy_webhook: str = "deploy"  # webhook path suffix

    # Notion CWI databases
    cwi_meetings_db_id: str = ""
    cwi_reports_db_id: str = ""
    cwi_backlog_db_id: str = ""
    cwi_transcriptions_db_id: str = "02803b2b-a641-4fb0-a819-f6e5141036e2"  # Controle DOCX (legado)
    cwi_projetos_db_id: str = "23f349e0-7965-8097-9bd3-f93cd36f52fb"  # Projetos — fonte real das transcricoes
    cwi_arquivo_db_id: str = ""
    cwi_watch_interval_seconds: int = 120

    # Expansao AI agent models
    ceo_model: str = "claude-sonnet-4-6"
    pm_model: str = "claude-sonnet-4-6"
    architect_model: str = "claude-sonnet-4-6"
    dev_model: str = "claude-sonnet-4-6"
    devops_model: str = "claude-haiku-4-5-20251001"
    qa_model: str = "claude-sonnet-4-6"
    marketing_model: str = "claude-haiku-4-5-20251001"

    # CWI agent models
    secretary_model: str = "claude-sonnet-4-6"
    pmo_model: str = "claude-sonnet-4-6"
    agile_coach_model: str = "claude-sonnet-4-6"
    product_model: str = "claude-sonnet-4-6"
    exec_report_model: str = "claude-opus-4-8"

    # API server
    aios_api_url: str = "http://localhost:8000"  # override with public URL after Coolify deploy

    # Orchestrator behavior
    human_in_the_loop: bool = True
    deploy_queue_mode: bool = True   # queue deploys after QA gate; execute daily at 22:00
    output_dir: str = "outputs/"
    log_level: str = "INFO"

    # Dashboard auth
    dashboard_user: str = "marcos"
    dashboard_password: str = "aios2026"
    dashboard_secret_key: str = "aios-dashboard-secret-change-me"



settings = Settings()  # type: ignore[call-arg]
