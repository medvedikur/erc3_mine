from .core import ActionExecutor
from .wiki import WikiManager, WikiMiddleware
from .security import SecurityManager, SecurityMiddleware

def get_executor(api, wiki_manager: WikiManager, security_manager: SecurityManager):
    middleware = [
        WikiMiddleware(wiki_manager)
    ]
    if security_manager:
        middleware.append(SecurityMiddleware(security_manager))
        
    return ActionExecutor(
        api=api,
        middleware=middleware
    )
