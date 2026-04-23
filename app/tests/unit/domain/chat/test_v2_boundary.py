from __future__ import annotations

from pathlib import Path


FORBIDDEN_V2_IMPORT_SNIPPETS = (
    "app.services.ai_context_service",
    "app.services.ai_chat_prompt_service",
    "app.services.conversation_memory_service",
    "app.services.ai_token_budget_service",
    "app.services.sanitization_service",
    "app.services.openai_service",
    "from app.services import ai_context_service",
    "from app.services import ai_chat_prompt_service",
    "from app.services import conversation_memory_service",
    "from app.services import ai_token_budget_service",
    "from app.services import sanitization_service",
    "from app.services import openai_service",
)

REMOVED_LEGACY_CHAT_FILES = (
    "app/schemas/ai_ask.py",
    "app/services/ai_chat_prompt_service.py",
    "app/services/ai_context_service.py",
    "app/services/ai_run_service.py",
    "app/services/ai_token_budget_service.py",
    "app/services/conversation_memory_service.py",
    "app/services/sanitization_service.py",
)

FORBIDDEN_V1_CHAT_SNIPPETS = (
    "@router.post(\"/ai/ask\"",
    "@router.post('/ai/ask'",
    "ai_chat_prompt_service =",
    "ai_context_service =",
    "ai_token_budget_service =",
    "conversation_memory_service =",
    "legacy_ai_",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def test_canonical_v2_flow_does_not_import_legacy_chat_modules() -> None:
    repo_root = _repo_root()
    v2_paths = [
        repo_root / "app" / "api" / "v2" / "endpoints" / "ai_chat.py",
        repo_root / "app" / "api" / "v2" / "deps" / "ai_chat.py",
    ]
    v2_paths.extend((repo_root / "app" / "domain" / "chat").glob("*.py"))

    for file_path in v2_paths:
        if not file_path.exists():
            continue
        content = file_path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_V2_IMPORT_SNIPPETS:
            assert forbidden not in content, f"Forbidden legacy import in {file_path}: {forbidden}"


def test_repo_has_no_legacy_v1_chat_endpoint_reference() -> None:
    repo_root = _repo_root()
    boundary_file = Path(__file__).resolve()
    scanned_files = [
        repo_root / "README.md",
        *list((repo_root / "docs").rglob("*.md")),
        *list((repo_root / "app").rglob("*.py")),
        *list((repo_root / "tests").rglob("*.py")),
    ]

    for file_path in scanned_files:
        if file_path.resolve() == boundary_file:
            continue
        content = file_path.read_text(encoding="utf-8")
        assert "/ai/ask" not in content, f"Legacy chat endpoint leaked in {file_path}"


def test_legacy_chat_files_are_removed() -> None:
    repo_root = _repo_root()
    for relative_path in REMOVED_LEGACY_CHAT_FILES:
        assert not (repo_root / relative_path).exists(), f"Legacy chat file reintroduced: {relative_path}"


def test_v1_ai_route_has_no_backward_compat_chat_aliases() -> None:
    ai_v1_path = _repo_root() / "app" / "api" / "routes" / "ai.py"
    content = ai_v1_path.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_V1_CHAT_SNIPPETS:
        assert forbidden not in content, f"Forbidden v1 chat compatibility residue in {ai_v1_path}: {forbidden}"
