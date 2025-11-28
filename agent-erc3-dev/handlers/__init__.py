from .core import ActionExecutor
from .wiki import WikiManager, WikiMiddleware

def get_executor(api, wiki_manager: WikiManager):
    return ActionExecutor(
        api=api,
        middleware=[
            WikiMiddleware(wiki_manager)
        ]
    )

