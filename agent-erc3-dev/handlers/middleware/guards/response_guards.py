"""
Response Guards - general response validation.

Guards:
- ResponseValidationMiddleware: Validates respond has proper message/links
"""
from ..base import ResponseGuard
from ...base import ToolContext
from utils import CLI_YELLOW, CLI_GREEN, CLI_CLR


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
