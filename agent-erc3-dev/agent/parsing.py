"""
LLM response parsing utilities.

Provides robust JSON extraction from LLM responses, handling:
- Markdown code blocks
- Truncated/broken JSON
- Multiple concatenated JSON objects
- Corrupted content detection (non-ASCII garbage)
"""
import json
import re
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class ParseResult:
    """
    Result of parsing LLM response.

    AICODE-NOTE: t017 FIX - Added to communicate parse status to runner.
    Previously, corrupted JSON was silently ignored, leading to hallucination.
    """
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    needs_retry: bool = False
    corruption_detected: bool = False


def _remove_message_values(content: str) -> str:
    """
    Remove 'message' field values from content for corruption checking.

    AICODE-NOTE: t023 FIX - Message values can contain any characters including
    Chinese (e.g., "是" = Yes). We need to exclude them from corruption checks.

    Args:
        content: JSON content to process

    Returns:
        Content with message values replaced by placeholder
    """
    # Pattern: "message" followed by : and a quoted string
    # Handle escaped quotes inside the string
    pattern = r'("message"\s*:\s*)"((?:[^"\\]|\\.)*)(")'

    def replacer(m):
        # Keep structure, replace value with placeholder
        return m.group(1) + '"__MESSAGE_PLACEHOLDER__"'

    return re.sub(pattern, replacer, content)


def _detect_corruption(content: str) -> Optional[str]:
    """
    Detect corrupted content in JSON.

    AICODE-NOTE: t017 FIX - LLM sometimes generates garbage characters
    (Chinese, emoji garbage, random unicode) especially when hitting token limits.

    AICODE-NOTE: t023 FIX - Chinese characters in 'message' field of respond tool
    are VALID (e.g., "是" = "Yes" for Chinese queries). Only flag Chinese in
    structural elements (tool names, outcome values, etc.)

    Returns:
        Error message if corruption detected, None otherwise.
    """
    # Check for suspicious non-ASCII sequences in key areas
    # Look specifically in action_queue area
    aq_start = content.find('"action_queue"')
    if aq_start == -1:
        return None

    aq_section = content[aq_start:]

    # AICODE-NOTE: t023 FIX - Remove message content before checking for Chinese
    # Pattern: "message": "..." or "message" : "..."
    # This allows Chinese/unicode in message values (valid user responses)
    aq_section_for_check = _remove_message_values(aq_section)

    # Pattern: unexpected characters in JSON values that break parsing
    # Chinese characters, random unicode blocks, etc.
    corruption_patterns = [
        # Chinese characters (common corruption) - but NOT in message values
        r'[\u4e00-\u9fff]',
        # Random unicode control chars
        r'[\u0000-\u0008\u000b\u000c\u000e-\u001f]',
        # Cyrillic in unexpected places (inside JSON keys/values that should be English)
        r'"[^"]*[\u0400-\u04ff][^"]*":\s*\[',  # Cyrillic in key names
    ]

    for pattern in corruption_patterns:
        match = re.search(pattern, aq_section_for_check)
        if match:
            # Get context around the corruption
            pos = match.start()
            context_start = max(0, pos - 20)
            context_end = min(len(aq_section_for_check), pos + 30)
            context = aq_section_for_check[context_start:context_end]
            return f"Corrupted characters in action_queue near: ...{context}..."

    return None


def _detect_truncated_action_queue(content: str, parsed_actions: list) -> Optional[str]:
    """
    Detect when action_queue appears truncated.

    AICODE-NOTE: t017 FIX - Sometimes JSON parses but action_queue is incomplete
    because it was truncated mid-object.

    Returns:
        Error message if truncation detected, None otherwise.
    """
    # If action_queue key exists in raw text but parsed as empty
    if '"action_queue"' in content:
        # Find the action_queue section
        aq_match = re.search(r'"action_queue"\s*:\s*\[', content)
        if aq_match:
            aq_start = aq_match.end()
            # Look for content after the opening bracket
            remaining = content[aq_start:].strip()

            # If there's content but we got empty list, likely truncated
            if remaining and not remaining.startswith(']') and len(parsed_actions) == 0:
                # Check for incomplete action object
                if '{' in remaining and remaining.count('{') > remaining.count('}'):
                    return "action_queue appears truncated - incomplete action object"

    # Check if any action looks incomplete
    for i, action in enumerate(parsed_actions):
        if not isinstance(action, dict):
            return f"Action {i} is not a valid object (got {type(action).__name__})"
        if 'tool' not in action:
            return f"Action {i} missing required 'tool' field"

    return None


def extract_json(content: str) -> Dict[str, Any]:
    """
    Extract JSON from LLM response.

    Handles:
    - Markdown code blocks (```json ... ```)
    - Multiple concatenated JSON objects
    - Truncated JSON with missing closing braces
    - Extra text before/after JSON

    Args:
        content: Raw LLM response text

    Returns:
        Parsed JSON as dict

    Raises:
        json.JSONDecodeError: If JSON cannot be extracted
    """
    content = content.strip()

    # Remove markdown code blocks
    if "```json" in content:
        start = content.find("```json") + 7
        end = content.find("```", start)
        if end > start:
            content = content[start:end].strip()
    elif "```" in content:
        start = content.find("```") + 3
        end = content.find("```", start)
        if end > start:
            content = content[start:end].strip()

    # Find JSON object boundaries
    if not content.startswith("{"):
        start = content.find("{")
        if start >= 0:
            content = content[start:]

    # Try to parse as-is first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try to repair common structural mistakes before more expensive fallbacks
    # AICODE-NOTE: t009 fix — Qwen/OpenAI models sometimes produce invalid JSON like:
    #   "plan": [
    #     {...},
    #     "step": "...",
    #     "status": "pending"
    #   ]
    # where "step"/"status" were intended as a plan object but braces were omitted.
    repaired = _try_fix_plan_step_status(content)
    if repaired and repaired != content:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # AICODE-NOTE: t087 FIX - Some models occasionally emit customer IDs as tool names in action_queue,
    # e.g. {"tool": "cust_freshfoods"}} instead of a proper customers_get call.
    # This breaks JSON parsing and results in empty action_queue. We repair this into:
    #   {"tool": "customers_get", "args": {"id": "cust_freshfoods"}}
    repaired = _try_fix_customer_id_as_tool(content)
    if repaired and repaired != content:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # AICODE-NOTE: t067 FIX - Try to fix missing closing braces in action_queue
    # LLM sometimes drops closing } for action objects when content is very long.
    # Must run BEFORE multi-JSON detection which would find small valid objects inside.
    repaired = _try_fix_action_queue_braces(content)
    if repaired and repaired != content:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # Try multi-JSON detection
    json_objects = _find_all_json_objects(content)
    if json_objects:
        # Prefer object with expected keys
        for obj in json_objects:
            if any(key in obj for key in ['thoughts', 'action_queue', 'plan', 'is_final']):
                return obj
        # Return the largest one
        return max(json_objects, key=lambda x: len(json.dumps(x)))

    # Try to fix truncated JSON
    result = _try_fix_truncated(content)
    if result:
        return result

    # Give up - raise the original error
    return json.loads(content)


def _try_fix_action_queue_braces(content: str) -> str | None:
    """
    AICODE-NOTE: t067 FIX - Repair missing closing braces in action_queue.

    When LLM generates very long content (e.g., wiki file content ~9KB),
    it sometimes drops closing } for action objects.

    Example broken structure:
        "action_queue": [
            {
                "tool": "wiki_update",
                "args": {
                    "file": "...",
                    "content": "...very long..."
                }           <-- closes args
            }               <-- MISSING! closes action object
        ],

    This function finds action_queue, counts braces, and inserts missing ones.
    """
    if '"action_queue"' not in content:
        return None

    # Find action_queue section
    aq_start = content.find('"action_queue"')
    if aq_start == -1:
        return None

    # Find opening [ after action_queue
    bracket_start = content.find('[', aq_start)
    if bracket_start == -1:
        return None

    # Find closing ] for action_queue by looking for pattern ],\n..."is_final"
    is_final_match = re.search(r'\],\s*\n\s*"is_final"', content)
    if not is_final_match:
        return None

    bracket_end = is_final_match.start() + 1  # position after ]

    aq_content = content[bracket_start:bracket_end + 1]

    # Count braces inside action_queue
    aq_open = aq_content.count('{')
    aq_close = aq_content.count('}')

    if aq_open <= aq_close:
        return None  # No missing braces

    missing = aq_open - aq_close

    # Find last } before ] and insert missing braces after it
    last_close = aq_content.rfind('}')
    if last_close == -1:
        return None

    fixed_aq = aq_content[:last_close + 1] + '\n    ' + '}' * missing + aq_content[last_close + 1:]

    return content[:bracket_start] + fixed_aq + content[bracket_end + 1:]


def _try_fix_customer_id_as_tool(content: str) -> str | None:
    """
    Repair malformed action items where a customer ID is used as a tool name.

    Examples seen in logs:
      {"tool": "cust_freshfoods"}}

    We convert them into a valid customers_get tool call:
      {"tool": "customers_get", "args": {"id": "cust_freshfoods"}}
    """
    if '"action_queue"' not in content:
        return None

    # Match: {"tool": "cust_xxx"} or {"tool": "cust_xxx"}} with optional trailing comma
    pattern = re.compile(
        r'\{\s*"tool"\s*:\s*"(?P<cust>cust_[a-z0-9_]+)"\s*\}\s*(?P<extra>\})?\s*(?P<trailing_comma>,?)',
        flags=re.IGNORECASE
    )

    def _repl(m: re.Match) -> str:
        cust = m.group('cust')
        trailing = m.group('trailing_comma') or ''
        return f'{{"tool": "customers_get", "args": {{"id": "{cust}"}}}}{trailing}'

    fixed, n = pattern.subn(_repl, content)
    if n <= 0:
        return None
    return fixed


def _try_fix_plan_step_status(content: str) -> str | None:
    """
    Fix common invalid JSON pattern where a plan array contains bare `"step": ...` / `"status": ...`
    pairs instead of an object.

    Returns:
        Fixed JSON string if a fix was applied, otherwise None.
    """
    if '"plan"' not in content:
        return None

    plan_key_idx = content.find('"plan"')
    if plan_key_idx < 0:
        return None

    open_idx = content.find('[', plan_key_idx)
    if open_idx < 0:
        return None

    close_idx = _find_matching_bracket(content, open_idx, '[', ']')
    if close_idx is None:
        return None

    plan_body = content[open_idx:close_idx + 1]

    # Replace multiline:
    #   "step": "...",
    #   "status": "pending"
    # with:
    #   {"step": "...", "status": "pending"}
    pattern = re.compile(
        r'\n(?P<indent>\s*)"step"\s*:\s*(?P<step>"(?:[^"\\]|\\.)*")\s*,\s*'
        r'\n(?P=indent)"status"\s*:\s*(?P<status>"(?:[^"\\]|\\.)*")\s*(?P<trailing_comma>,?)',
        flags=re.MULTILINE
    )

    fixed_body, n = pattern.subn(
        r'\n\g<indent>{"step": \g<step>, "status": \g<status>}\g<trailing_comma>',
        plan_body
    )
    if n <= 0:
        return None

    return content[:open_idx] + fixed_body + content[close_idx + 1:]


def _find_matching_bracket(text: str, start_idx: int, open_char: str, close_char: str) -> int | None:
    """Find matching closing bracket/brace for a given opening bracket/brace index."""
    depth = 0
    in_string = False
    escape_next = False

    for i in range(start_idx, len(text)):
        char = text[i]
        if escape_next:
            escape_next = False
            continue
        if char == '\\' and in_string:
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i

    return None


def _find_all_json_objects(text: str) -> list:
    """Find all valid JSON objects in concatenated text."""
    results = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            # Try to find matching closing brace
            depth = 0
            in_string = False
            escape_next = False
            for j in range(i, len(text)):
                char = text[j]
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\' and in_string:
                    escape_next = True
                    continue
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        depth += 1
                    elif char == '}':
                        depth -= 1
                        if depth == 0:
                            try:
                                obj = json.loads(text[i:j+1])
                                results.append(obj)
                            except json.JSONDecodeError:
                                pass
                            i = j + 1
                            break
            else:
                i += 1
        else:
            i += 1
    return results


def _try_fix_truncated(content: str) -> Dict[str, Any] | None:
    """Try to fix truncated JSON by adding missing braces."""
    open_braces = content.count("{")
    close_braces = content.count("}")
    open_brackets = content.count("[")
    close_brackets = content.count("]")

    # Try adding missing closing braces
    if open_braces > close_braces:
        fixed = content.rstrip().rstrip(",")
        fixed += "}" * (open_braces - close_braces)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Try trimming from end to find valid JSON
    for i in range(len(content), 0, -1):
        if content[i-1] == "}":
            try:
                return json.loads(content[:i])
            except json.JSONDecodeError:
                continue

    # Try adding missing brackets and braces
    if open_brackets > close_brackets:
        fixed = content.rstrip().rstrip(",")
        fixed += "]" * (open_brackets - close_brackets)
        fixed += "}" * (open_braces - close_braces)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    return None


class OpenAIUsage:
    """
    Usage statistics compatible with OpenAI format.

    Mimics the OpenAI usage object structure expected by erc3 SDK.
    """
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

    def model_dump(self, mode: str = 'python', include=None, exclude=None,
                   by_alias: bool = False, exclude_unset: bool = False,
                   exclude_defaults: bool = False, exclude_none: bool = False,
                   round_trip: bool = False, warnings: bool = True) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens
        }


def parse_llm_response(raw_content: str) -> ParseResult:
    """
    Parse LLM response and return structured result.

    AICODE-NOTE: t017 FIX - Wrapper around extract_json that:
    1. Detects corrupted content BEFORE parsing
    2. Validates action_queue structure AFTER parsing
    3. Returns ParseResult with needs_retry flag

    This allows runner.py to inform the agent about parse failures
    so it can regenerate the response instead of hallucinating.

    Args:
        raw_content: Raw LLM response text

    Returns:
        ParseResult with success status and error details if any
    """
    # Step 1: Check for corruption BEFORE parsing
    corruption_error = _detect_corruption(raw_content)
    if corruption_error:
        return ParseResult(
            success=False,
            error=corruption_error,
            needs_retry=True,
            corruption_detected=True
        )

    # Step 2: Try to parse JSON
    try:
        parsed = extract_json(raw_content)
    except json.JSONDecodeError as e:
        return ParseResult(
            success=False,
            error=f"JSON parse error: {e}",
            needs_retry=True
        )

    # Step 3: Validate action_queue
    action_queue = parsed.get("action_queue", [])
    truncation_error = _detect_truncated_action_queue(raw_content, action_queue)
    if truncation_error:
        return ParseResult(
            success=False,
            data=parsed,  # Include partial data for debugging
            error=truncation_error,
            needs_retry=True
        )

    return ParseResult(
        success=True,
        data=parsed
    )
