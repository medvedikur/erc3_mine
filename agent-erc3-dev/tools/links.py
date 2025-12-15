"""
Link extraction and validation.

Provides the LinkExtractor class for auto-detecting entity links
from response messages.
"""
import re
from typing import Any, Dict, List, Optional


class LinkExtractor:
    """
    Extracts and validates entity links from response messages.

    Handles:
    - Prefixed IDs (proj_*, emp_*, cust_*)
    - Bare employee usernames (name_surname pattern)
    - Mutation entity tracking
    - Link deduplication and validation
    """

    # Non-employee patterns that look like usernames but aren't
    NON_EMPLOYEE_PATTERNS = frozenset([
        "cv_engineering", "edge_ai", "machine_learning", "deep_learning",
        "data_engineering", "cloud_architecture", "backend_development",
        "frontend_development", "mobile_development", "devops_engineering",
        "security_engineering", "project_management", "technical_writing",
        "time_slice", "work_category", "deal_phase", "account_manager",
        "employee_id", "project_id", "customer_id", "next_offset",
    ])

    # Prefix to kind mapping
    TYPE_MAP = {"proj": "project", "emp": "employee", "cust": "customer"}

    def extract_from_message(self, message: str) -> List[Dict[str, str]]:
        """
        Extract entity links from message text.

        Args:
            message: Response message text

        Returns:
            List of link dicts with 'id' and 'kind' keys
        """
        links = []

        # Find prefixed IDs (proj_, emp_, cust_)
        prefixed_ids = re.findall(r'\b((?:proj|emp|cust)_[a-z0-9_]+)\b', str(message))
        for found_id in prefixed_ids:
            prefix = found_id.split('_')[0]
            if prefix in self.TYPE_MAP:
                links.append({"id": found_id, "kind": self.TYPE_MAP[prefix]})

        # Find bare employee usernames (name_surname pattern)
        # AICODE-NOTE: Updated regex to support alphanumeric IDs like iv5n_030, 6KR2_044
        potential_users = re.findall(r'\b([a-zA-Z0-9]+(?:_[a-zA-Z0-9]+)+)\b', str(message))
        for pu in potential_users:
            if not pu.startswith(('proj_', 'emp_', 'cust_')):
                if pu not in self.NON_EMPLOYEE_PATTERNS:
                    links.append({"id": pu, "kind": "employee"})
            if pu.startswith('emp_'):
                # Also extract the username part after emp_ prefix
                links.append({"id": pu[4:], "kind": "employee"})

        return links

    def normalize_links(self, links: List[Any]) -> List[Dict[str, str]]:
        """
        Normalize links from various formats to standard dict format.

        Handles:
        - String IDs (auto-detect kind from prefix)
        - Dicts with various field naming conventions

        Args:
            links: Raw links list from agent response

        Returns:
            Normalized list of link dicts
        """
        if not links:
            return []

        normalized = []
        for link in links:
            if isinstance(link, str):
                # Agent passed string ID directly - auto-detect kind
                prefix = link.split('_')[0] if '_' in link else ""
                normalized.append({
                    "kind": self.TYPE_MAP.get(prefix, ""),
                    "id": link
                })
            else:
                # Agent passed dict - support multiple field naming conventions
                kind = (link.get("kind") or link.get("Kind") or
                        link.get("type") or link.get("Type", ""))
                link_id = (link.get("id") or link.get("ID") or
                          link.get("value") or link.get("Value", ""))
                normalized.append({"kind": kind, "id": link_id})

        return normalized

    def add_mutation_entities(
        self,
        links: List[Dict[str, str]],
        mutation_entities: List[Dict[str, str]],
        current_user: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        Add mutation entities and current user to links.

        Args:
            links: Existing links
            mutation_entities: Entities from mutation operations
            current_user: Current user ID to add

        Returns:
            Links with mutation entities added
        """
        result = links.copy()

        for entity in mutation_entities:
            if not self._link_exists(result, entity.get("id"), entity.get("kind")):
                result.append(entity)

        if current_user:
            if not self._link_exists(result, current_user, "employee"):
                result.append({"id": current_user, "kind": "employee"})

        return result

    def add_search_entities(
        self,
        links: List[Dict[str, str]],
        search_entities: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """
        Add search entities to links (for read-only operations).

        Args:
            links: Existing links
            search_entities: Entities from search operations

        Returns:
            Links with search entities added
        """
        result = links.copy()

        for entity in search_entities:
            if not self._link_exists(result, entity.get("id"), entity.get("kind")):
                result.append(entity)

        return result

    def deduplicate(self, links: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        Remove duplicate links.

        Args:
            links: Links list with potential duplicates

        Returns:
            Deduplicated links list
        """
        seen = set()
        unique = []
        for link in links:
            key = (link.get("id"), link.get("kind"))
            if key not in seen:
                seen.add(key)
                unique.append(link)
        return unique

    def validate_employee_links(
        self,
        links: List[Dict[str, str]],
        api: Any
    ) -> List[Dict[str, str]]:
        """
        Validate employee links via API.

        Removes links for non-existent employees.

        Args:
            links: Links to validate
            api: ERC3 API client

        Returns:
            Validated links list
        """
        from erc3.erc3 import client

        validated = []
        for link in links:
            if link.get("kind") == "employee":
                try:
                    req = client.Req_GetEmployee(id=link.get("id"))
                    api.dispatch(req)
                    validated.append(link)
                except Exception as e:
                    # Keep link if error is not "not found"
                    if "not found" not in str(e).lower() and "404" not in str(e):
                        validated.append(link)
            else:
                validated.append(link)

        return validated

    def _link_exists(
        self,
        links: List[Dict[str, str]],
        link_id: str,
        kind: str
    ) -> bool:
        """Check if a link already exists in the list."""
        return any(
            l.get("id") == link_id and l.get("kind") == kind
            for l in links
        )
