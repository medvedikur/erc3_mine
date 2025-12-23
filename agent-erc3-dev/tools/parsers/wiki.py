"""
Wiki tool parsers.
"""
from typing import Any
from erc3.erc3 import client
from ..registry import ToolParser, ParseContext


@ToolParser.register("wiki_list", "wikilist", "listwiki")
def _parse_wiki_list(ctx: ParseContext) -> Any:
    """List all wiki pages."""
    return client.Req_ListWiki()


@ToolParser.register("wiki_load", "wikiload", "loadwiki", "readwiki")
def _parse_wiki_load(ctx: ParseContext) -> Any:
    """Load a specific wiki page by path."""
    file_arg = ctx.args.get("file") or ctx.args.get("path") or ctx.args.get("page")
    if not file_arg:
        return None
    return client.Req_LoadWiki(file=file_arg)


@ToolParser.register("wiki_search", "wikisearch", "searchwiki")
def _parse_wiki_search(ctx: ParseContext) -> Any:
    """Search wiki pages by regex query."""
    query = (ctx.args.get("query_regex") or ctx.args.get("query") or
             ctx.args.get("query_semantic") or ctx.args.get("search_term"))
    return client.Req_SearchWiki(query_regex=query)


@ToolParser.register("wiki_update", "wikiupdate", "updatewiki")
def _parse_wiki_update(ctx: ParseContext) -> Any:
    """Update or create a wiki page."""
    content = ctx.args.get("content")
    file_path = ctx.args.get("file") or ctx.args.get("path")

    # AICODE-NOTE: t067 fix. LLM often passes escaped newlines (\\n) in JSON strings.
    # Decode them to actual newlines for proper wiki content.
    if content and isinstance(content, str):
        content = content.replace('\\n', '\n').replace('\\t', '\t')

    # AICODE-NOTE: t067 fix. LLM may corrupt Unicode when copying wiki content.
    # If content was recently loaded via wiki_load and LLM is copying it,
    # use the original content to preserve exact bytes.
    #
    # IMPORTANT: We now use '_loaded_wiki_content_api' which comes from API,
    # not from local WikiManager cache. This ensures consistency with evaluation.
    if content and ctx.context and hasattr(ctx.context, 'shared'):
        # First try API-loaded content (preferred for rename operations)
        loaded_content = ctx.context.shared.get('_loaded_wiki_content_api', {})
        if not loaded_content:
            # Fallback to local cache content
            loaded_content = ctx.context.shared.get('_loaded_wiki_content', {})
        if loaded_content:
            # Check if LLM content matches any loaded content (after normalization)
            for loaded_path, original_content in loaded_content.items():
                if _content_matches_approximately(content, original_content):
                    # Use original content to preserve Unicode
                    content = original_content
                    break

    return client.Req_UpdateWiki(
        file=file_path,
        content=content,
        changed_by=ctx.args.get("changed_by")
    )


def _content_matches_approximately(llm_content: str, original_content: str) -> bool:
    """
    Check if LLM-provided content matches original content approximately.

    LLMs may change Unicode characters when copying:
    - EN DASH (U+2013) → NON-BREAKING HYPHEN (U+2011)
    - Curly quotes → straight quotes
    - Various dash types

    Args:
        llm_content: Content from LLM
        original_content: Original content from wiki_load

    Returns:
        True if contents match approximately (same text, different Unicode)
    """
    if not llm_content or not original_content:
        return False

    # Quick length check - if vastly different lengths, not a match
    if abs(len(llm_content) - len(original_content)) > len(original_content) * 0.1:
        return False

    # Normalize both strings for comparison
    def normalize(s: str) -> str:
        # Replace various dashes with standard hyphen
        s = s.replace('\u2011', '-')  # NON-BREAKING HYPHEN
        s = s.replace('\u2013', '-')  # EN DASH
        s = s.replace('\u2014', '-')  # EM DASH
        s = s.replace('\u2010', '-')  # HYPHEN
        # Replace various quotes with standard quotes
        s = s.replace('\u201c', '"')  # LEFT DOUBLE QUOTATION
        s = s.replace('\u201d', '"')  # RIGHT DOUBLE QUOTATION
        s = s.replace('\u2018', "'")  # LEFT SINGLE QUOTATION
        s = s.replace('\u2019', "'")  # RIGHT SINGLE QUOTATION
        s = s.replace('\u00ab', '"')  # LEFT-POINTING DOUBLE ANGLE
        s = s.replace('\u00bb', '"')  # RIGHT-POINTING DOUBLE ANGLE
        # Normalize whitespace
        s = ' '.join(s.split())
        return s

    return normalize(llm_content) == normalize(original_content)
