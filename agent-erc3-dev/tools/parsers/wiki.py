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
    # AICODE-NOTE: t067 fix. LLM often passes escaped newlines (\\n) in JSON strings.
    # Decode them to actual newlines for proper wiki content.
    if content and isinstance(content, str):
        content = content.replace('\\n', '\n').replace('\\t', '\t')
    return client.Req_UpdateWiki(
        file=ctx.args.get("file") or ctx.args.get("path"),
        content=content,
        changed_by=ctx.args.get("changed_by")
    )
