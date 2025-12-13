"""
Argument normalization utilities.

Handles common LLM hallucinations and field name variations.
"""
from typing import Any, Dict, Optional


def normalize_args(args: dict) -> dict:
    """
    Normalize argument keys to handle common LLM hallucinations.

    Maps incorrectly named fields to their correct names.

    Args:
        args: Raw arguments dict

    Returns:
        Normalized arguments dict
    """
    normalized = args.copy()

    # Common mappings (hallucination -> correct key)
    # NOTE: These are general mappings. Tool-specific mappings should be in parsers.
    mappings = {
        # Wiki
        "query_semantic": "query_regex",
        "query": "query_regex",
        "page_filter": "page",
        "page_includes": "page",

        # Employees/Time - keep employee_id -> employee
        "employee_id": "employee",
        "user_id": "employee",
        "username": "employee",

        # NOTE: "project" -> "id" mapping REMOVED - it breaks time_get fallback!
        # Each parser should handle its own field names.
    }

    for bad_key, good_key in mappings.items():
        if bad_key in normalized and good_key not in normalized:
            normalized[good_key] = normalized[bad_key]

    return normalized


def inject_context(args: dict, context: Any) -> dict:
    """
    Inject current user ID into args if missing.

    Auto-fills auditing fields like logged_by and changed_by.

    Args:
        args: Arguments dict
        context: Parse context with security manager

    Returns:
        Arguments with injected user ID
    """
    if not context or not hasattr(context, 'shared'):
        return args

    security_manager = context.shared.get('security_manager')
    if not security_manager or not security_manager.current_user:
        return args

    current_user = security_manager.current_user

    # Fields that always require the current user
    user_fields = ["logged_by", "changed_by"]

    for field in user_fields:
        if field not in args or not args[field]:
            args[field] = current_user

    return args


def detect_placeholders(args: dict) -> Optional[str]:
    """
    Detect placeholder values in arguments.

    Catches when the model tries to use values it doesn't have yet.

    Args:
        args: Arguments dict to check

    Returns:
        Error message if placeholder found, None otherwise
    """
    placeholder_patterns = [
        "<<<", ">>>",           # <<<FILL_FROM_SEARCH>>>
        "FILL_",                # FILL_FROM_SEARCH, etc.
        "{RESULT", "{VALUE",    # Template-style
    ]

    # Skip free-text fields
    free_text_fields = {"message", "content", "text", "notes", "description", "reason"}

    for key, value in args.items():
        if isinstance(value, str) and key.lower() not in free_text_fields:
            value_upper = value.upper()
            for pattern in placeholder_patterns:
                if pattern in value_upper:
                    return (
                        f"Argument '{key}' contains placeholder value '{value}'. "
                        "You cannot use placeholders! Wait for the previous tool "
                        "results before calling dependent tools. Execute tools one "
                        "at a time when values depend on previous results."
                    )
    return None


def normalize_team_roles(team_data: list) -> list:
    """
    Normalize team role names to valid TeamRole enum values.

    Args:
        team_data: List of team member dicts

    Returns:
        Normalized team data
    """
    role_mappings = {
        "tester": "QA", "testing": "QA", "quality": "QA",
        "quality control": "QA", "qc": "QA", "qa": "QA",
        "developer": "Engineer", "dev": "Engineer",
        "devops": "Ops", "operations": "Ops",
        "ui": "Designer", "ux": "Designer",
        "lead": "Lead", "manager": "Lead", "pm": "Lead", "project manager": "Lead",
        "engineer": "Engineer", "designer": "Designer", "ops": "Ops", "other": "Other",
    }
    valid_roles = ["Lead", "Engineer", "Designer", "QA", "Ops", "Other"]

    normalized = []
    for member in team_data:
        if isinstance(member, dict):
            role = member.get("role", "Other")
            normalized_role = role_mappings.get(role.lower(), role) if role else "Other"
            if normalized_role not in valid_roles:
                normalized_role = "Other"
            normalized.append({
                "employee": member.get("employee"),
                "time_slice": member.get("time_slice", 0.0),
                "role": normalized_role
            })
    return normalized
