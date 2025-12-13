"""
Bonus policy enricher for salary updates.
Extracts bonus policy from wiki or task instructions.
"""
import re
from typing import Any, Dict, Iterable, List, Optional

from utils import CLI_YELLOW, CLI_CLR


class BonusPolicyEnricher:
    """
    Looks up and applies bonus policies from wiki or task instructions.

    Bonus policies can be:
    - Flat amount: +100, +500, etc.
    - Percentage: +10%, +15%, etc.
    """

    def lookup_bonus_policy(
        self,
        wiki_manager: Any,
        instructions: str
    ) -> Optional[Dict[str, Any]]:
        """
        Look up bonus policy from wiki or parse from instructions.

        Args:
            wiki_manager: WikiManager instance for wiki search
            instructions: Task text to parse for bonus info

        Returns:
            Dict with 'type' (flat/percent), 'amount', and 'message' or None
        """
        text = (instructions or "").lower()
        keywords = ["ny bonus", "new year bonus", "holiday bonus", "eoy bonus", "bonus tradition"]
        mentions_bonus = any(k in text for k in keywords)

        # First try wiki search if bonus is mentioned
        if mentions_bonus and wiki_manager and wiki_manager.pages:
            search_terms = ["bonus", "NY bonus", "New Year bonus", "EoY bonus"]
            snippets = self._search_wiki_for_bonus(wiki_manager, search_terms)
            parsed = self._parse_bonus_snippet_list(snippets)
            if parsed:
                return parsed

        # Then try parsing from instructions
        value = self._parse_bonus_from_instructions(text)
        if value:
            return {
                "type": value["type"],
                "amount": value["amount"],
                "message": f"AUTO-HINT: Interpreting '+{value['raw']}' from instructions as {value['type']} bonus."
            }
        return None

    def apply_bonus_policy(
        self,
        current_salary: float,
        policy: Dict[str, Any]
    ) -> Optional[float]:
        """
        Apply bonus policy to current salary.

        Args:
            current_salary: Current salary value
            policy: Policy dict from lookup_bonus_policy

        Returns:
            New salary or None if policy invalid
        """
        amount = policy.get("amount")
        if amount is None:
            return None
        if policy.get("type") == "flat":
            return current_salary + amount
        if policy.get("type") == "percent":
            return current_salary * (1 + amount / 100.0)
        return None

    def _parse_bonus_from_instructions(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse bonus amount from task instructions."""
        if not text:
            return None
        percentage = re.search(r"\+\s*(\d+)\s*%", text)
        if percentage:
            return {"type": "percent", "amount": float(percentage.group(1)), "raw": percentage.group(1) + "%"}
        flat = re.search(r"\+\s*\$?(\d+)(?!\s*%)", text)
        if flat:
            return {"type": "flat", "amount": float(flat.group(1)), "raw": flat.group(1)}
        return None

    def _search_wiki_for_bonus(self, wiki_manager: Any, terms: Iterable[str]) -> List[str]:
        """Search wiki for bonus-related content."""
        snippets = []
        for term in terms:
            try:
                response = wiki_manager.search(term, top_k=3)
                snippets.append(response)
            except Exception as e:
                print(f"  {CLI_YELLOW}Wiki bonus search failed for '{term}': {e}{CLI_CLR}")
        return snippets

    def _parse_bonus_snippet(self, snippet: str) -> Optional[Dict[str, Any]]:
        """Parse bonus info from a wiki snippet."""
        if not snippet:
            return None
        percent = re.search(r"(\d+)\s*%", snippet)
        if percent:
            return {
                "type": "percent",
                "amount": float(percent.group(1)),
                "message": f"AUTO-HINT: Wiki snippet suggests +{percent.group(1)}%: {snippet.strip()[:120]}..."
            }
        flat_currency = re.search(r"(\d+)\s*(?:EUR|euro|bucks|usd)", snippet, re.I)
        if flat_currency:
            return {
                "type": "flat",
                "amount": float(flat_currency.group(1)),
                "message": f"AUTO-HINT: Wiki snippet suggests +{flat_currency.group(1)} currency: {snippet.strip()[:120]}..."
            }
        flat_plain = re.search(r"\b\+?(\d+)\b", snippet)
        if flat_plain:
            return {
                "type": "flat",
                "amount": float(flat_plain.group(1)),
                "message": f"AUTO-HINT: Wiki snippet suggests +{flat_plain.group(1)} units: {snippet.strip()[:120]}..."
            }
        return None

    def _parse_bonus_snippet_list(self, snippets: List[str]) -> Optional[Dict[str, Any]]:
        """Parse first valid bonus from list of snippets."""
        for snippet in snippets:
            parsed = self._parse_bonus_snippet(snippet)
            if parsed:
                return parsed
        return None
