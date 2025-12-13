"""
Wiki page summarizer for generating concise actionable summaries.
Uses rule-based extraction to identify key information.
"""
import re
from typing import Dict


class WikiSummarizer:
    """
    Generates concise actionable summaries from wiki pages.
    Uses rule-based extraction to identify:
    - Action requirements (MUST, REQUIRED, MANDATORY)
    - Prohibitions (CANNOT, NEVER, MUST NOT)
    - Formats and examples (code blocks, patterns)
    - Key section headers
    """

    # Patterns that indicate actionable content
    ACTION_PATTERNS = [
        (r'(?:must|shall|required?|mandatory|always)\s+([^.!?\n]{10,150}[.!?]?)', 'MUST'),
        (r'(?:cannot|must\s+not|shall\s+not|never|prohibited?|forbidden)\s+([^.!?\n]{10,150}[.!?]?)', 'CANNOT'),
        (r'(?:should|recommended?)\s+([^.!?\n]{10,150}[.!?]?)', 'SHOULD'),
    ]

    # Patterns for structured data (formats, codes, examples)
    FORMAT_PATTERNS = [
        r'format\s*(?:is)?:?\s*[`\n]([^`\n]{5,100})',  # "format: X" or "format is: X"
        r'```(?:text)?\s*\n?([^`]{5,200})\n?```',       # code blocks
        r'(?:example|e\.g\.)[:\s]+([^.\n]{10,100})',    # examples
    ]

    @classmethod
    def generate_summary(cls, content: str, path: str, max_length: int = 800) -> str:
        """
        Generate a concise actionable summary from wiki page content.

        Args:
            content: Full wiki page content
            path: Page path (e.g., "merger.md")
            max_length: Maximum summary length

        Returns:
            Condensed summary with key actionable items
        """
        summary_parts = []

        # 1. Extract title (first H1 or H2)
        title_match = re.search(r'^#+ (.+)$', content, re.MULTILINE)
        if title_match:
            summary_parts.append(f"**{title_match.group(1)}**")

        # 2. Extract section headers (H2, H3)
        headers = re.findall(r'^##+ (.+)$', content, re.MULTILINE)
        if headers:
            # Keep only most important headers (max 5)
            key_headers = [h for h in headers[:7] if len(h) < 50]
            if key_headers:
                summary_parts.append(f"Sections: {', '.join(key_headers[:5])}")

        # 3. Extract action requirements
        actions = []
        content_lower = content.lower()

        for pattern, action_type in cls.ACTION_PATTERNS:
            matches = re.findall(pattern, content_lower, re.IGNORECASE)
            for match in matches[:3]:  # Max 3 per type
                clean_match = match.strip()
                if len(clean_match) > 20:  # Skip too short matches
                    actions.append(f"- {action_type}: {clean_match[:100]}")

        if actions:
            summary_parts.append("**Key Rules:**")
            summary_parts.extend(actions[:6])  # Max 6 rules total

        # 4. Extract formats and examples (important for CC codes, etc.)
        formats = []
        for pattern in cls.FORMAT_PATTERNS:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches[:2]:
                clean = match.strip()
                if clean and len(clean) > 5:
                    formats.append(f"  `{clean[:60]}`")

        if formats:
            summary_parts.append("**Formats/Examples:**")
            summary_parts.extend(formats[:3])

        # 5. Special handling for known document types
        if 'merger' in path.lower():
            # Look for acquisition info - company name in bold after "acquired by"
            acq_match = re.search(r'acquired by[^*]*\*\*([^*]+)\*\*', content, re.IGNORECASE)
            if acq_match:
                company_name = acq_match.group(1).strip()
                if len(company_name) > 5:  # Valid company name
                    summary_parts.insert(1, f"Acquired by: **{company_name}**")

            # NOTE: We intentionally DO NOT include CC code requirements in summary
            # because it causes agents to ask for CC code BEFORE identifying the project.
            # The agent should:
            # 1. Find the correct project first (get authorization hints)
            # 2. Load merger.md for full details if needed
            # 3. THEN ask for CC code if missing, including project link in response
            # The CC code check happens in safety.py middleware when time_log is attempted.

        if 'rulebook' in path.lower():
            # Look for key permission rules
            if 'salary' in content_lower:
                summary_parts.append("Contains salary access/modification rules")
            if 'security' in content_lower or 'permission' in content_lower:
                summary_parts.append("Contains security/permission policies")

        # 6. Build final summary
        summary = '\n'.join(summary_parts)

        # Truncate if too long
        if len(summary) > max_length:
            summary = summary[:max_length-50] + '\n... [use wiki_load for full content]'

        # Add footer with search hint
        summary += f"\n\nUse `wiki_load(\"{path}\")` or `wiki_search` for details."

        return summary

    @classmethod
    def generate_all_summaries(cls, pages: Dict[str, str]) -> Dict[str, str]:
        """Generate summaries for all wiki pages."""
        summaries = {}
        for path, content in pages.items():
            summaries[path] = cls.generate_summary(content, path)
        return summaries
