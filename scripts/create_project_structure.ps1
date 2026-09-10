param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Ensure-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path | Out-Null
        Write-Host "[created] dir  $Path"
    }
    else {
        Write-Host "[exists]  dir  $Path"
    }
}

function Ensure-File {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$ForceWrite
    )

    if ((Test-Path -LiteralPath $Path) -and -not $ForceWrite) {
        Write-Host "[exists]  file $Path"
        return
    }

    $parent = Split-Path -Parent $Path
    if ($parent) {
        Ensure-Directory -Path $parent
    }

    Set-Content -LiteralPath $Path -Value "" -Encoding UTF8
    if ($ForceWrite) {
        Write-Host "[updated] file $Path"
    }
    else {
        Write-Host "[created] file $Path"
    }
}

$root = (Resolve-Path -LiteralPath .).Path
$target = Join-Path $root $ProjectRoot
Ensure-Directory -Path $target

$directories = @(
    "app",
    "app/agents",
    "app/agents/memory",
    "app/db",
    "app/prompts",
    "app/routes",
    "app/schemas",
    "app/services",
    "app/utils",
    "docs",
    "scripts",
    "src",
    "src/rm_agent",
    "tests"
)

$files = @(
    "Dockerfile",
    "main.py",
    "pyproject.toml",
    "README.md",
    ".env",
    ".env.example",
    "app/__init__.py",
    "app/config.py",
    "app/constants.py",
    "app/main.py",
    "app/agents/__init__.py",
    "app/agents/base.py",
    "app/agents/clarification.py",
    "app/agents/execution.py",
    "app/agents/orchestrator.py",
    "app/agents/portfolio_insights.py",
    "app/agents/relationship_intelligence.py",
    "app/agents/router.py",
    "app/agents/synthesizer.py",
    "app/agents/tool_output_utils.py",
    "app/db/__init__.py",
    "app/db/models.py",
    "app/db/session.py",
    "app/prompts/__init__.py",
    "app/prompts/clarification.py",
    "app/prompts/direct_reply.py",
    "app/prompts/memory.py",
    "app/prompts/portfolio_insights.py",
    "app/prompts/relationship_intelligence.py",
    "app/prompts/replan.py",
    "app/prompts/router.py",
    "app/prompts/synthesizer.py",
    "app/routes/__init__.py",
    "app/routes/agent.py",
    "app/schemas/__init__.py",
    "app/schemas/agent.py",
    "app/schemas/citations.py",
    "app/schemas/evaluation.py",
    "app/schemas/internal.py",
    "app/services/__init__.py",
    "app/services/audit.py",
    "app/services/checkpointer.py",
    "app/services/citations.py",
    "app/services/entitlements.py",
    "app/services/evaluation.py",
    "app/services/guardrails.py",
    "app/services/llm.py",
    "app/services/llm_core.py",
    "app/services/llm_gemini.py",
    "app/services/llm_openai.py",
    "app/services/llm_router.py",
    "app/services/llm_unique.py",
    "app/services/network.py",
    "app/services/observability.py",
    "app/utils/__init__.py",
    "docs/00_DEVELOPER_OVERVIEW.md",
    "docs/unique_sdk_ai_completion_advanced_api.md",
    "docs/unique_sdk_cli_guide.md",
    "docs/unique_sdk_content_search_folder_api.md",
    "docs/unique_sdk_messaging_api.md",
    "docs/unique_sdk_overview_setup_architecture.md",
    "docs/unique_sdk_space_user_group_api.md",
    "docs/unique_sdk_utilities.md",
    "docs/unique_sdk_webhooks_and_tutorials.md",
    "docs/unique_toolkit_agentic_framework_core.md",
    "docs/unique_toolkit_agentic_framework_managers.md",
    "docs/unique_toolkit_agentic_table_and_tutorials.md",
    "docs/unique_toolkit_overview_chat_knowledge_events.md",
    "scripts/init_db.py",
    "src/rm_agent/__init__.py",
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/test_audit_service.py",
    "tests/test_checkpointer_inmemory.py",
    "tests/test_citations_service.py",
    "tests/test_db_session.py",
    "tests/test_evaluation_service.py",
    "tests/test_health.py",
    "tests/test_llm_router.py",
    "tests/test_llm_unique.py",
    "tests/test_network_service.py",
    "tests/test_orchestrator_citations_evaluations.py",
    "tests/test_sse_listener_service.py",
    "tests/test_stream_endpoint.py",
    "tests/test_streaming_service.py",
    "tests/test_unique_model_apis.py",
    "tests/test_unique_runtime_service.py",
    "tests/test_unique_webhook_api.py",
    "tests/test_webhook_sse_events_api.py"
)

foreach ($dir in $directories) {
    Ensure-Directory -Path (Join-Path $target $dir)
}

foreach ($file in $files) {
    Ensure-File -Path (Join-Path $target $file) -ForceWrite:$Force
}

Write-Host ""
Write-Host "Project structure scaffold complete: $target"
Write-Host "Use -Force to overwrite existing files with empty content."
