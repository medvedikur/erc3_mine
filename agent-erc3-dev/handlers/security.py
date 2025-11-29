from typing import Any, Optional
from erc3.erc3 import client
from .base import ToolContext, Middleware

class SecurityManager:
    """
    Manages user identity and enforcing security policies (redaction).
    """
    def __init__(self):
        self.is_public = False
        self.current_user = None

    def update_identity(self, who_am_i_resp: client.Resp_WhoAmI):
        """Update identity state from API response"""
        self.is_public = getattr(who_am_i_resp, 'is_public', False)
        # Handle different possible attribute names for user_id based on API version
        # Try: user_id, id, employee_id, username, employee, current_user
        candidates = ['user_id', 'id', 'employee_id', 'username', 'current_user']
        
        user_id = None
        for attr in candidates:
            val = getattr(who_am_i_resp, attr, None)
            if val:
                user_id = val
                break
        
        # Fallback: check if 'user' object exists and has id
        if not user_id:
            user_obj = getattr(who_am_i_resp, 'user', None)
            if user_obj:
                if isinstance(user_obj, dict):
                    user_id = user_obj.get('id') or user_obj.get('user_id') or user_obj.get('employee_id') or user_obj.get('username')
                else:
                    user_id = getattr(user_obj, 'id', getattr(user_obj, 'user_id', getattr(user_obj, 'employee_id', getattr(user_obj, 'username', None))))

        # IMPORTANT: Ensure user_id is a string if it exists
        if user_id:
            self.current_user = str(user_id)
        else:
            self.current_user = None
            
        print(f"🔒 Security State Updated: Public={self.is_public}, User={self.current_user}")

    def redact_result(self, result: Any) -> Any:
        """
        Redact sensitive fields from API responses based on current identity.
        """
        if not self.is_public:
            return result

        # Rule: Guests cannot see IDs
        
        # Projects
        if isinstance(result, client.Resp_ProjectSearchResults):
            for p in result.projects:
                p.id = "REDACTED_SECURITY_RESTRICTED"
        
        elif isinstance(result, client.Resp_ListProjects):
            for p in result.projects:
                p.id = "REDACTED_SECURITY_RESTRICTED"
        
        elif isinstance(result, client.Resp_GetProject):
            result.id = "REDACTED_SECURITY_RESTRICTED"

        # Employees
        elif isinstance(result, client.Resp_SearchEmployees):
            for e in result.employees:
                e.id = "REDACTED_SECURITY_RESTRICTED"
        
        elif isinstance(result, client.Resp_ListEmployees):
            for e in result.employees:
                e.id = "REDACTED_SECURITY_RESTRICTED"
        
        elif isinstance(result, client.Resp_GetEmployee):
            result.id = "REDACTED_SECURITY_RESTRICTED"

        return result

class SecurityMiddleware(Middleware):
    """
    Middleware to inject security manager into context and enforce permissions.
    """
    def __init__(self, manager: SecurityManager):
        self.manager = manager

    def process(self, ctx: ToolContext) -> None:
        ctx.shared['security_manager'] = self.manager
        
        # --- Permission Enforcement ---
        current_user = self.manager.current_user
        if not current_user:
            return

        # 1. Project Modifications (Status/Team) require Lead/Owner role
        if isinstance(ctx.model, (client.Req_UpdateProjectStatus, client.Req_UpdateProjectTeam)):
            self._enforce_project_ownership(ctx, ctx.model.id, current_user)

    def _enforce_project_ownership(self, ctx: ToolContext, project_id: str, user_id: str):
        try:
            # Bypass middleware chain to fetch project details directly from API
            # Try positional arg first, then project_id kwarg
            try:
                project = ctx.api.get_project(project_id)
            except TypeError:
                project = ctx.api.get_project(project_id=project_id)
            
            # Normalize lead/owner fields (handle string or object)
            lead = getattr(project, 'lead', None)
            owner = getattr(project, 'owner', None)
            
            if hasattr(lead, 'id'): lead = lead.id
            if hasattr(owner, 'id'): owner = owner.id
            
            # Check authorization
            is_authorized = False
            if lead and lead == user_id: is_authorized = True
            if owner and owner == user_id: is_authorized = True
            
            if not is_authorized:
                ctx.stop_execution = True
                msg = f"Security Violation: User '{user_id}' is not the Lead/Owner of project '{project_id}' (Lead: {lead}). Action denied."
                print(f"🛑 {msg}")
                ctx.results.append(f"Action ({ctx.model.__class__.__name__}): FAILED\nError: {msg}")
                
        except Exception as e:
            print(f"⚠️ SecurityMiddleware: Could not verify project permissions: {e}")
            # We don't block if verification fails (e.g. project not found), 
            # we let the actual action fail naturally.
