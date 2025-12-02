from typing import Any, Dict, List, Optional
import re
import datetime
from erc3.erc3 import client
from pydantic import BaseModel, Field

# Define simplified tool models where needed, or map directly to erc3.client

class Req_Respond(BaseModel):
    message: str
    outcome: str = Field(..., description="One of: ok_answer, ok_not_found, denied_security, none_clarification_needed, none_unsupported, error_internal")
    links: List[dict] = []


class ParseError:
    """
    Represents a parsing error with a message to return to the LLM.
    Used instead of None to provide meaningful feedback.
    """
    def __init__(self, message: str, tool: str = None):
        self.message = message
        self.tool = tool
    
    def __str__(self):
        if self.tool:
            return f"Tool '{self.tool}': {self.message}"
        return self.message

# --- RUNTIME PATCH FOR LIBRARY BUG ---
# The erc3 library enforces non-optional lists for skills/wills in Req_UpdateEmployeeInfo,
# causing empty lists to be sent (and triggering events) even when we only want to update salary.
# We patch the model definition at runtime to make them Optional.
def _patch_update_employee_model(model_class, class_name):
    """Patch a Req_UpdateEmployeeInfo model to make skills/wills/notes/location/department Optional."""
    from typing import Optional, List
    try:
        if hasattr(model_class, 'model_fields'):
            # Pydantic v2
            fields_to_patch = ['skills', 'wills', 'notes', 'location', 'department']
            for field in fields_to_patch:
                if field in model_class.model_fields:
                    model_class.model_fields[field].default = None
                    # For list types, make them Optional
                    if field in ['skills', 'wills']:
                        from erc3.erc3 import dtos
                        model_class.model_fields[field].annotation = Optional[List[dtos.SkillLevel]]
                    else:
                        model_class.model_fields[field].annotation = Optional[str]
            # Rebuild model
            if hasattr(model_class, 'model_rebuild'):
                model_class.model_rebuild()
        else:
            # Pydantic v1
            fields_to_patch = ['skills', 'wills', 'notes', 'location', 'department']
            for field in fields_to_patch:
                if field in model_class.__fields__:
                    model_class.__fields__[field].required = False
                    model_class.__fields__[field].default = None
        print(f"🔧 Patched {class_name} to support optional fields.")
        return True
    except Exception as e:
        print(f"⚠️ Failed to patch {class_name}: {e}")
        return False

try:
    from erc3.erc3 import dtos
    _patch_update_employee_model(dtos.Req_UpdateEmployeeInfo, "dtos.Req_UpdateEmployeeInfo")
except Exception as e:
    print(f"⚠️ Failed to patch dtos.Req_UpdateEmployeeInfo: {e}")

try:
    # Also patch client.Req_UpdateEmployeeInfo since SafeReq inherits from it
    _patch_update_employee_model(client.Req_UpdateEmployeeInfo, "client.Req_UpdateEmployeeInfo")
except Exception as e:
    print(f"⚠️ Failed to patch client.Req_UpdateEmployeeInfo: {e}")

# --- SAFE MODEL WRAPPERS ---
class SafeReq_UpdateEmployeeInfo(client.Req_UpdateEmployeeInfo):
    """
    Wrapper to ensure we don't send null values for optional fields,
    which would overwrite existing data with nulls/defaults in the backend.
    """
    def model_dump(self, **kwargs):
        # Always exclude None to prevent overwriting with nulls
        kwargs['exclude_none'] = True
        data = super().model_dump(**kwargs)
        # Also remove empty lists for skills/wills/notes to prevent clearing them/triggering events
        keys_to_remove = ['skills', 'wills', 'notes', 'location', 'department']
        for k in keys_to_remove:
            if k in data and (data[k] == [] or data[k] == "" or data[k] is None):
                del data[k]
        return data
    
    def dict(self, **kwargs):
        # Fallback for Pydantic v1 or older usage
        kwargs['exclude_none'] = True
        data = super().dict(**kwargs)
        keys_to_remove = ['skills', 'wills', 'notes', 'location', 'department']
        for k in keys_to_remove:
            if k in data and (data[k] == [] or data[k] == "" or data[k] is None):
                del data[k]
        return data

    def model_dump_json(self, **kwargs):
        # Ensure JSON serialization also excludes None and empty lists
        # We can't easily modify the JSON string output of super().model_dump_json
        # So we dump to dict first, then json.dumps?
        # Or rely on the fact that dispatch might use model_dump/dict?
        # If dispatch uses model_dump_json, we are in trouble if we can't hook it.
        # But we can use our model_dump logic!
        import json
        data = self.model_dump(**kwargs)
        return json.dumps(data)
# ---------------------------

def _normalize_args(args: dict) -> dict:
    """Normalize argument keys to handle common LLM hallucinations"""
    normalized = args.copy()
    
    # Common mappings (hallucination -> correct key)
    mappings = {
        # Wiki
        "query_semantic": "query_regex",
        "query": "query_regex",
        "page_filter": "page",
        "page_includes": "page",
        
        # Employees/Time
        "employee_id": "employee",
        "id": "employee", # Context dependent, but handled in specific blocks
        "user_id": "employee",
        "username": "employee",
        
        # Projects
        "project_id": "id",
        "project": "id", # For get_project
        "name": "query", # Common hallucination for search

        # Time Log
        "project_id": "project", # For time_log
    }

    for bad_key, good_key in mappings.items():
        if bad_key in normalized and good_key not in normalized:
            normalized[good_key] = normalized[bad_key]
            
    return normalized

def _inject_context(args: dict, context: Any) -> dict:
    """Inject current user ID into args if missing"""
    if not context or not hasattr(context, 'shared'):
        return args
        
    security_manager = context.shared.get('security_manager')
    if not security_manager or not security_manager.current_user:
        return args
        
    current_user = security_manager.current_user
    
    # Fields that always require the current user acting as the modifier
    user_fields = ["logged_by", "changed_by"]
    
    for field in user_fields:
        if field not in args or not args[field]:
            args[field] = current_user
                
    return args

def _detect_placeholders(args: dict) -> Optional[str]:
    """Detect placeholder values in arguments that indicate the model is trying to use values it doesn't have yet."""
    placeholder_patterns = [
        "<<<", ">>>",           # <<<FILL_FROM_SEARCH>>>
        "FILL_", "PLACEHOLDER", # Common placeholders
        "{RESULT", "{VALUE",    # Template-style
        "TBD", "TODO",          # To be determined
        "...",                  # Ellipsis placeholder
    ]
    
    for key, value in args.items():
        if isinstance(value, str):
            value_upper = value.upper()
            for pattern in placeholder_patterns:
                if pattern in value_upper:
                    return f"Argument '{key}' contains placeholder value '{value}'. You cannot use placeholders! Wait for the previous tool results before calling dependent tools. Execute tools one at a time when values depend on previous results."
    return None

def parse_action(action_dict: dict, context: Any = None) -> Optional[Any]:
    """Parse action dict into Pydantic model for Erc3Client"""
    tool = action_dict.get("tool", "").lower().replace("_", "").replace("-", "").replace("/", "")
    
    # Flatten args - merge args into action_dict to handle both nested and flat structures
    raw_args = action_dict.get("args", {})
    if raw_args:
        combined_args = {**action_dict, **raw_args}
    else:
        combined_args = action_dict
        
    # Use combined_args for lookups
    args = combined_args.copy()
    
    # SAFETY: Detect placeholder values before processing
    placeholder_error = _detect_placeholders(args)
    if placeholder_error:
        return ParseError(placeholder_error, tool=tool)
    
    # Normalize args
    args = _normalize_args(args)
    
    # Inject Context (Auto-fill user ID for auditing fields)
    if context:
        args = _inject_context(args, context)
        
    # Helper to get current user for defaults
    current_user = None
    if context and hasattr(context, 'shared'):
        sm = context.shared.get('security_manager')
        if sm:
            current_user = sm.current_user

    # --- Tool Mappings ---

    # Employees
    if tool in ["whoami", "me", "identity"]:
        return client.Req_WhoAmI()
    
    if tool in ["employeeslist", "listemployees"]:
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        return client.Req_ListEmployees(**kwargs)
    
    if tool in ["employeessearch", "searchemployees"]:
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        return client.Req_SearchEmployees(
            query=args.get("query") or args.get("name") or args.get("query_regex"), # Handle 'name' and 'query_regex' alias
            location=args.get("location"),
            department=args.get("department"),
            manager=args.get("manager"),
            **kwargs
        )
    
    if tool in ["employeesget", "getemployee"]:
        emp_id = args.get("id") or args.get("employee_id") or args.get("employee")
        username = args.get("username")
        
        # Smart dispatch: if ID is missing but username/name is provided, use search instead
        if not emp_id and (username or args.get("name")):
            query = username or args.get("name")
            kwargs = {}
            kwargs["offset"] = int(args.get("offset", 0))
            kwargs["limit"] = int(args.get("limit", 5))
            return client.Req_SearchEmployees(query=query, **kwargs)
            
        if not emp_id:
            # Default to current user if asking for "my" profile implicitly?
            if current_user:
                emp_id = current_user
            else:
                print("⚠ 'id' argument missing in 'get_employee'. Skipping to force LLM retry.")
                return None
            
        return client.Req_GetEmployee(id=emp_id)
    
    if tool in ["employeesupdate", "updateemployee", "salaryupdate", "updatesalary"]: # Added aliases
        # Build kwargs filtering out Nones to avoid validation error for optional lists
        update_args = {
            "employee": args.get("employee") or args.get("id") or args.get("employee_id") or current_user,
            "notes": args.get("notes"),
            "salary": args.get("salary"),
            "location": args.get("location"),
            "department": args.get("department"),
            "skills": args.get("skills"),
            "wills": args.get("wills"),
            "changed_by": args.get("changed_by") # Auto-filled by context
        }
        # Filter out None values
        valid_args = {k: v for k, v in update_args.items() if v is not None}
        
        return SafeReq_UpdateEmployeeInfo(**valid_args)

    # Wiki
    if tool in ["wikilist", "listwiki"]:
        return client.Req_ListWiki()
    
    if tool in ["wikiload", "loadwiki", "readwiki"]:
        file_arg = args.get("file") or args.get("path") or args.get("page")
        if not file_arg:
            print("⚠ 'file' argument missing in 'wiki_load'. Skipping to force LLM retry.")
            return None
        return client.Req_LoadWiki(file=file_arg)
    
    if tool in ["wikisearch", "searchwiki"]:
        # Smart arg mapping for common hallucinations
        query = args.get("query_regex") or args.get("query") or args.get("query_semantic") or args.get("search_term")
        return client.Req_SearchWiki(query_regex=query)
    
    if tool in ["wikiupdate", "updatewiki"]:
        return client.Req_UpdateWiki(
            file=args.get("file") or args.get("path"),
            content=args.get("content"),
            changed_by=args.get("changed_by")
        )

    # Customers
    if tool in ["customerslist", "listcustomers"]:
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        return client.Req_ListCustomers(**kwargs)
    
    if tool in ["customersget", "getcustomer"]:
        cust_id = args.get("id") or args.get("customer_id")
        if not cust_id:
            print("⚠ 'id' argument missing in 'get_customer'. Skipping to force LLM retry.")
            return None
        return client.Req_GetCustomer(id=cust_id)
    
    if tool in ["customerssearch", "searchcustomers"]:
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        return client.Req_SearchCustomers(
            query=args.get("query") or args.get("query_regex"),
            deal_phase=args.get("deal_phase"),
            locations=args.get("locations"),
            account_managers=args.get("account_managers"),
            **kwargs
        )

    # Projects
    if tool in ["projectslist", "listprojects"]:
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        return client.Req_ListProjects(**kwargs)
    
    if tool in ["projectsget", "getproject"]:
        proj_id = args.get("id") or args.get("project_id")
        if not proj_id:
            print("⚠ 'id' argument missing in 'get_project'. Skipping to force LLM retry.")
            return None
        return client.Req_GetProject(id=proj_id)
    
    if tool in ["projectssearch", "searchprojects"]:
        status_arg = args.get("status")
        if isinstance(status_arg, str):
            status = [status_arg]
        elif isinstance(status_arg, list):
            # If list is empty, we set to None to avoid "match nothing" behavior if that's the default
            status = status_arg if status_arg else None
        else:
            status = None
            
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))

        # Handle team filter (member parameter)
        # API expects ProjectTeamFilter with employee_id, role, min_time_slice
        team_filter = None
        member_id = args.get("member") or args.get("team_member") or args.get("employee_id")
        if member_id:
            from erc3.erc3 import dtos
            team_filter = dtos.ProjectTeamFilter(
                employee_id=member_id,
                role=args.get("role"),  # Optional: filter by role (Lead, Engineer, etc.)
                min_time_slice=float(args.get("min_time_slice", 0.0))
            )

        search_args = {
            "query": args.get("query") or args.get("query_regex"),
            "customer_id": args.get("customer_id"),
            "status": status,
            "team": team_filter,
            # Default include_archived to True to find all projects by default
            "include_archived": bool(args.get("include_archived", True)),
            **kwargs
        }
        # Filter out None values to respect Pydantic defaults/optionality
        valid_search_args = {k: v for k, v in search_args.items() if v is not None}

        return client.Req_SearchProjects(**valid_search_args)
    
    if tool in ["projectsteamupdate", "updateprojectteam"]:
        return client.Req_UpdateProjectTeam(
            id=args.get("id") or args.get("project_id"),
            team=args.get("team"),
            changed_by=args.get("changed_by")
        )
        
    if tool in ["projectsstatusupdate", "updateprojectstatus", "projectssetstatus"]:
        # Only status updates - requires valid status
        status = args.get("status")
        if not status:
            return ParseError(
                "projects_status_update requires 'status' field. Valid values: 'idea', 'exploring', 'active', 'paused', 'archived'",
                tool="projects_status_update"
            )
        return client.Req_UpdateProjectStatus(
            id=args.get("id") or args.get("project_id"),
            status=status,
            changed_by=args.get("changed_by")
        )
    
    # Handle unsupported "projects_update" - there's no general metadata update API
    if tool in ["projectsupdate", "updateproject"]:
        # This is often hallucinated by LLMs - no such API exists
        return ParseError(
            "Tool 'projects_update' does not exist. There is no API for updating project metadata. "
            "Available project tools: projects_status_update (change status), projects_team_update (change team). "
            "If you need to add dependencies or metadata, this feature is NOT SUPPORTED.",
            tool="projects_update"
        )

    # Time
    if tool in ["timelog", "logtime"]:
        target_emp = args.get("employee") or args.get("employee_id")
        # If target employee is missing, assume self (me)
        if not target_emp:
            target_emp = current_user
            
        # Determine date: Explicit arg > Simulated Date > Real Today
        date_val = args.get("date")
        if not date_val:
            if context and hasattr(context, 'shared'):
                sm = context.shared.get('security_manager')
                if sm and hasattr(sm, 'today') and sm.today:
                    date_val = sm.today
        
        if not date_val:
            date_val = datetime.date.today().isoformat()
            
        return client.Req_LogTimeEntry(
            employee=target_emp,
            project=args.get("project") or args.get("project_id"),
            customer=args.get("customer"),
            date=date_val,
            hours=float(args.get("hours", 0)),
            work_category=args.get("work_category", "dev"),
            notes=args.get("notes", ""),
            billable=bool(args.get("billable", True)),
            status=args.get("status", "draft"),
            logged_by=args.get("logged_by") # Auto-filled by context
        )
    
    if tool in ["timeget", "gettime"]:
        entry_id = args.get("id")
        if not entry_id:
            print("⚠ 'id' argument missing in 'get_time'. Skipping to force LLM retry.")
            return None
        return client.Req_GetTimeEntry(id=entry_id)

    if tool in ["timesearch", "searchtime"]:
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        return client.Req_SearchTimeEntries(
            employee=args.get("employee") or args.get("employee_id") or current_user, # Default to self search
            project=args.get("project") or args.get("project_id"),
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            billable=args.get("billable", ""),
            **kwargs
        )
        
    if tool in ["timeupdate", "updatetime"]:
         return client.Req_UpdateTimeEntry(
            id=args.get("id"),
            date=args.get("date"),
            hours=float(args.get("hours", 0)),
            work_category=args.get("work_category"),
            notes=args.get("notes"),
            billable=args.get("billable"),
            status=args.get("status"),
            changed_by=args.get("changed_by")
        )

    # Response
    if tool in ["respond", "answer", "reply"]:
        # OpenAI models often use PascalCase - handle both cases
        message = (args.get("message") or args.get("Message") or 
                   args.get("text") or args.get("Text") or 
                   args.get("response") or args.get("Response") or 
                   args.get("answer") or args.get("Answer") or 
                   args.get("content") or args.get("Content"))
        if not message:
            message = "No message provided."
        
        # Outcome Fallback Logic - handle PascalCase
        outcome = args.get("outcome") or args.get("Outcome")
        if not outcome:
            # Try to infer outcome from message content
            msg_lower = str(message).lower()
            if "cannot" in msg_lower or "unable to" in msg_lower or "could not" in msg_lower:
                if "tool" in msg_lower or "system" in msg_lower:
                    outcome = "none_unsupported"
                elif "permission" in msg_lower or "access" in msg_lower or "allow" in msg_lower or "restricted" in msg_lower:
                    outcome = "denied_security"
                else:
                    outcome = "none_clarification_needed"
            else:
                # Default to ok_answer if not negative
                outcome = "ok_answer"
            print(f"⚠ 'outcome' missing. Inferred '{outcome}' from message.")

        # Link Auto-Detection - handle PascalCase
        links = args.get("links") or args.get("Links") or []
        
        # Normalize links format if using PascalCase (Kind/ID instead of kind/id)
        if links:
            normalized_links = []
            for link in links:
                normalized_links.append({
                    "kind": link.get("kind") or link.get("Kind", ""),
                    "id": link.get("id") or link.get("ID", "")
                })
            links = normalized_links
        
        # For ok_answer outcomes, we need to ensure links are properly populated
        if outcome == "ok_answer":
            # Step 1: If agent didn't provide any links, auto-detect from message text
            if not links:
                # Regex to find IDs in message
                # IDs often look like: proj_..., emp_..., cust_...
                # Pattern: \b(proj|emp|cust)_[a-z0-9_]+\b
                ids = re.findall(r'\b((?:proj|emp|cust)_[a-z0-9_]+)\b', str(message))
                for found_id in ids:
                    # Simple heuristic to guess type
                    type_map = {
                        "proj": "project", 
                        "emp": "employee", 
                        "cust": "customer"
                    }
                    prefix = found_id.split('_')[0]
                    if prefix in type_map:
                        links.append({"id": found_id, "kind": type_map[prefix]})
                
                # Special fallback for simple employee usernames like 'felix_baum' or 'timo_van_dijk' that lack 'emp_' prefix
                # This is risky as it might match common words, but for this benchmark we know the format.
                # Pattern: one or more lowercase word segments separated by underscores (handles 2, 3, or more parts)
                potential_users = re.findall(r'\b([a-z]+(?:_[a-z]+)+)\b', str(message))
                for pu in potential_users:
                    if not pu.startswith('proj_') and not pu.startswith('emp_') and not pu.startswith('cust_'):
                         links.append({"id": pu, "kind": "employee"})
                    
                    # Handle IDs that were prefixed with 'emp_' in text but should be bare
                    if pu.startswith('emp_'):
                         real_id = pu[4:]
                         links.append({"id": real_id, "kind": "employee"})
            
            # Step 2: ALWAYS add current user to links (they are the actor who performed the action)
            # This is needed even if the agent already provided some links (e.g., for time logging,
            # the agent might include the target employee but forget the person who logged the time)
            if current_user:
                current_user_in_links = any(
                    link.get("id") == current_user and link.get("kind") == "employee"
                    for link in links
                )
                if not current_user_in_links:
                    links.append({"id": current_user, "kind": "employee"})
                
        return client.Req_ProvideAgentResponse(
            message=str(message),
            outcome=outcome,
            links=links
        )

    # Unknown tool - return helpful error
    return ParseError(
        f"Unknown tool '{tool}'. Available tools: employees_list, employees_get, employees_update, "
        f"projects_list, projects_get, projects_status_update, projects_team_update, "
        f"time_list, time_add, time_update, wiki_search, respond. "
        f"Check tool name spelling.",
        tool=tool
    )
