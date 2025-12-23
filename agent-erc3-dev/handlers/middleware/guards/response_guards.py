"""
Response Guards - general response validation.

Guards:
- ResponseValidationMiddleware: Validates respond has proper message/links
- LeadWikiCreationGuard: Ensures all project leads have wiki pages created (t069)
"""
import re
from ..base import ResponseGuard
from ...base import ToolContext
from utils import CLI_YELLOW, CLI_GREEN, CLI_RED, CLI_CLR


class LeadWikiCreationGuard(ResponseGuard):
    """
    AICODE-NOTE: t069 FIX - Validates all project leads have wiki pages created.

    Uses _state_ref to read LIVE mutation state (not stale snapshot).
    Only blocks if task is about creating wiki for leads AND some leads are missing.
    """

    target_outcomes = {"ok_answer"}

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        # Check if this is a lead wiki creation task
        task_text = ctx.shared.get('task_text', '').lower()
        is_lead_wiki_task = (
            'lead' in task_text and
            ('wiki' in task_text or 'create' in task_text or 'page' in task_text)
        )
        if not is_lead_wiki_task:
            return

        # Get live state reference for current mutation data
        state = ctx.shared.get('_state_ref')
        if not state:
            return

        # Get leads found via projects_get
        found_leads = state.found_project_leads
        if not found_leads:
            # No leads tracked - either no projects_get calls or no leads in projects
            return

        # Get wiki pages created - use LIVE state, not stale ctx.shared
        created_wiki_files = set()
        for entity in state.mutation_entities:
            if entity.get('kind') == 'wiki':
                wiki_file = entity.get('id', '')
                # Extract employee ID from leads/bAsk_XXX.md format
                if wiki_file.startswith('leads/'):
                    emp_id = wiki_file.replace('leads/', '').replace('.md', '')
                    created_wiki_files.add(emp_id)

        # Check for missing leads
        missing_leads = found_leads - created_wiki_files

        print(f"  [t069 guard] found_leads={len(found_leads)}, created_wiki={len(created_wiki_files)}, missing={len(missing_leads)}")

        if missing_leads:
            missing_list = ', '.join(sorted(missing_leads)[:10])
            if len(missing_leads) > 10:
                missing_list += f"... (+{len(missing_leads) - 10} more)"

            ctx.stop_execution = True
            ctx.results.append(
                f"🚫 INCOMPLETE: You found {len(found_leads)} project leads but only created wiki pages for {len(created_wiki_files)}.\n"
                f"Missing wiki pages for: {missing_list}\n\n"
                f"Create the missing wiki pages before responding!"
            )


class ResponseValidationMiddleware(ResponseGuard):
    """
    Validates respond calls have proper message and links.
    Auto-generates message for empty responses after mutations.
    """

    target_outcomes = {"ok_answer"}

    def _check(self, ctx: ToolContext, outcome: str) -> None:
        message = ctx.model.message or ""

        # Check if mutations were performed this session
        had_mutations = ctx.shared.get('had_mutations', False)
        mutation_entities = ctx.shared.get('mutation_entities', [])

        # Validate: If mutations happened and outcome is ok_answer, message should describe what was done
        if had_mutations and message in ["", "No message provided.", "No message provided"]:
            print(f"  {CLI_YELLOW}⚠️ Response Validation: Empty message after mutation. Injecting summary...{CLI_CLR}")
            # Auto-generate a minimal message from mutation_entities
            entity_descriptions = []
            for entity in mutation_entities:
                kind = entity.get("kind", "entity")
                eid = entity.get("id", "unknown")
                entity_descriptions.append(f"{kind}: {eid}")
            if entity_descriptions:
                ctx.model.message = f"Action completed. Affected entities: {', '.join(entity_descriptions)}"
                print(f"  {CLI_GREEN}✓ Auto-generated message: {ctx.model.message[:100]}...{CLI_CLR}")
