"""
Response tool parsers.
"""
from typing import Any
from erc3.erc3 import client
from ..registry import ToolParser, ParseContext
from ..links import LinkExtractor


@ToolParser.register("respond", "answer", "reply")
def _parse_respond(ctx: ParseContext) -> Any:
    """Submit final response to user."""
    args = ctx.args
    link_extractor = LinkExtractor()

    # Extract query_specificity
    query_specificity = (args.get("query_specificity") or args.get("querySpecificity") or
                         args.get("specificity") or "unspecified")
    if isinstance(query_specificity, str):
        query_specificity = query_specificity.lower().strip()
    if ctx.context and hasattr(ctx.context, 'shared'):
        ctx.context.shared['query_specificity'] = query_specificity

    # Extract message
    message = (args.get("message") or args.get("Message") or
               args.get("text") or args.get("Text") or
               args.get("response") or args.get("Response") or
               args.get("answer") or args.get("Answer") or
               args.get("content") or args.get("Content") or
               args.get("details") or args.get("Details") or
               args.get("body") or args.get("Body"))

    # FALLBACK: If agent mistakenly put message content in query_specificity, use it
    if not message:
        qs_value = args.get("query_specificity") or args.get("querySpecificity")
        if qs_value and isinstance(qs_value, str) and len(qs_value) > 50:
            # Likely a message mistakenly put in query_specificity
            message = qs_value
        else:
            message = "No message provided."

    # Extract/infer outcome
    outcome = args.get("outcome") or args.get("Outcome")
    if not outcome:
        msg_lower = str(message).lower()
        if "cannot" in msg_lower or "unable to" in msg_lower or "could not" in msg_lower:
            if "tool" in msg_lower or "system" in msg_lower:
                outcome = "none_unsupported"
            elif any(w in msg_lower for w in ["permission", "access", "allow", "restricted"]):
                outcome = "denied_security"
            else:
                outcome = "none_clarification_needed"
        else:
            outcome = "ok_answer"

    # Extract and normalize links
    raw_links = args.get("links") or args.get("Links") or []
    links = link_extractor.normalize_links(raw_links)

    # Auto-detect links from message for ok_answer outcomes only
    # AICODE-NOTE: We only auto-extract for ok_answer because:
    # - ok_not_found: entities mentioned are NOT the answer
    # - denied_security: we clear links anyway (line 103)
    # - none_*: clarification requests shouldn't auto-link
    if not links and outcome == 'ok_answer':
        links = link_extractor.extract_from_message(str(message))

    # Validate employee links
    if links and ctx.context:
        links = link_extractor.validate_employee_links(links, ctx.context.api)

    # Add mutation/search entities - with smart filtering based on outcome
    # AICODE-NOTE: Critical fix for link accuracy. Problems fixed:
    # 1. Don't add current_user unless they are the TARGET of the action (not just authorizer)
    # 2. Don't add search_entities for ok_not_found - those entities are NOT the answer
    # 3. For ok_not_found, only keep entities explicitly mentioned as "found" in message
    if ctx.context:
        had_mutations = ctx.context.shared.get('had_mutations', False)
        mutation_entities = ctx.context.shared.get('mutation_entities', [])
        search_entities = ctx.context.shared.get('search_entities', [])

        if had_mutations:
            # For mutations, add ONLY the mutated entities, NOT current_user
            # current_user should only be added if THEY were modified (e.g., updating own profile)
            # The benchmark checks for links that are the ANSWER, not the authorizer
            for entity in mutation_entities:
                if not link_extractor._link_exists(links, entity.get("id"), entity.get("kind")):
                    links.append(entity)
        elif outcome == 'ok_answer' and search_entities:
            # For ok_answer, add search entities that represent THE answer
            # But limit to entities that appear in the message to avoid over-linking
            message_lower = str(message).lower()
            for entity in search_entities:
                entity_id = entity.get("id", "")
                # Only add if entity ID appears in message (it's part of the answer)
                if entity_id and entity_id.lower() in message_lower:
                    if not link_extractor._link_exists(links, entity_id, entity.get("kind")):
                        links.append(entity)
        # For ok_not_found: do NOT add search_entities - they are NOT the answer!

    # Deduplicate
    links = link_extractor.deduplicate(links)

    # Clear links for error/denied outcomes
    # AICODE-NOTE: For denied_security responses (like salary queries), we should NOT
    # link entities that were mentioned in the denial - this could leak information
    if outcome in ("error_internal", "denied_security"):
        links = []

    return client.Req_ProvideAgentResponse(
        message=str(message),
        outcome=outcome,
        links=links
    )
