from .core import ActionExecutor
from .wiki import WikiManager, WikiMiddleware
from .security import SecurityManager, SecurityMiddleware
from .safety import ProjectMembershipMiddleware

def get_executor(api, wiki_manager: WikiManager, security_manager: SecurityManager, task=None):
    middleware = [
        WikiMiddleware(wiki_manager),
        ProjectMembershipMiddleware()
    ]
    if security_manager:
        middleware.append(SecurityMiddleware(security_manager))
        
    return ActionExecutor(
        api=api,
        middleware=middleware,
        task=task
    )
