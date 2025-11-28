from typing import Any, Dict, List, Optional
from erc3.erc3 import client
from pydantic import BaseModel, Field

# Define simplified tool models where needed, or map directly to erc3.client

class Req_Respond(BaseModel):
    message: str
    outcome: str = Field(..., description="One of: ok_answer, ok_not_found, denied_security, none_clarification_needed, none_unsupported, error_internal")
    links: List[dict] = []

def parse_action(action_dict: dict) -> Optional[Any]:
    """Parse action dict into Pydantic model for Erc3Client"""
    tool = action_dict.get("tool", "").lower().replace("_", "").replace("-", "").replace("/", "")
    
    # Flatten args - merge args into action_dict to handle both nested and flat structures
    args = action_dict.get("args", {})
    if args:
        # Create a combined dictionary for lookups, but keep args for backward compat if needed
        combined_args = {**action_dict, **args}
    else:
        combined_args = action_dict
        
    # Use combined_args for lookups instead of args
    # But some existing logic uses 'args' specifically, so we'll update 'args' to be the combined source of truth for params
    args = combined_args

    # Common mappings
    # Employees
    if tool in ["whoami", "me", "identity"]:
        return client.Req_WhoAmI()
    
    if tool in ["employeeslist", "listemployees"]:
        kwargs = {}
        # If args limit is set, use it. Otherwise default to 5 (small batch to avoid limits).
        # We assume server handles pagination.
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        return client.Req_ListEmployees(**kwargs)
    
    if tool in ["employeessearch", "searchemployees"]:
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        return client.Req_SearchEmployees(
            query=args.get("query"),
            location=args.get("location"),
            department=args.get("department"),
            manager=args.get("manager"),
            **kwargs
        )
    
    if tool in ["employeesget", "getemployee"]:
        emp_id = args.get("id") or args.get("employee_id")
        username = args.get("username")
        
        # Smart dispatch: if ID is missing but username is provided, use search instead
        if not emp_id and username:
            kwargs = {}
            kwargs["offset"] = int(args.get("offset", 0))
            kwargs["limit"] = int(args.get("limit", 0))
            return client.Req_SearchEmployees(query=username, **kwargs)
            
        if not emp_id:
            print("⚠ 'id' argument missing in 'get_employee'. Skipping to force LLM retry.")
            return None
            
        return client.Req_GetEmployee(id=emp_id)
    
    if tool in ["employeesupdate", "updateemployee"]:
        return client.Req_UpdateEmployeeInfo(
            employee=args.get("employee") or args.get("id"),
            notes=args.get("notes"),
            salary=args.get("salary"),
            location=args.get("location"),
            department=args.get("department"),
            skills=args.get("skills"),
            wills=args.get("wills"),
            changed_by=args.get("changed_by")
        )

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
        return client.Req_SearchWiki(query_regex=args.get("query_regex") or args.get("query"))
    
    if tool in ["wikiupdate", "updatewiki"]:
        return client.Req_UpdateWiki(
            file=args.get("file"),
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
        cust_id = args.get("id")
        if not cust_id:
            print("⚠ 'id' argument missing in 'get_customer'. Skipping to force LLM retry.")
            return None
        return client.Req_GetCustomer(id=cust_id)
    
    if tool in ["customerssearch", "searchcustomers"]:
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        return client.Req_SearchCustomers(
            query=args.get("query"),
            deal_phase=args.get("deal_phase"),
            locations=args.get("locations"),
            account_managers=args.get("account_managers"),
            **kwargs
        )

    # Projects
    if tool in ["projectslist", "listprojects"]:
        # Only pass limit/offset if provided, to avoid overriding defaults or hitting strict limits
        # However, due to API quirks or strict validation, we might need defaults.
        # If strict limits (limit=0) are enforced, we default to 0.
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))
        
        return client.Req_ListProjects(**kwargs)
    
    if tool in ["projectsget", "getproject"]:
        proj_id = args.get("id")
        if not proj_id:
            print("⚠ 'id' argument missing in 'get_project'. Skipping to force LLM retry.")
            return None
        return client.Req_GetProject(id=proj_id)
    
    if tool in ["projectssearch", "searchprojects"]:
        status_arg = args.get("status")
        if isinstance(status_arg, str):
            status = [status_arg]
        elif isinstance(status_arg, list):
            status = status_arg
        else:
            status = []
            
        # Default limit to 0 to avoid "page limit exceeded: 10 > 0"
        kwargs = {}
        kwargs["offset"] = int(args.get("offset", 0))
        kwargs["limit"] = int(args.get("limit", 5))

        return client.Req_SearchProjects(
            query=args.get("query"),
            customer_id=args.get("customer_id"),
            status=status,
            include_archived=bool(args.get("include_archived", False)),
            **kwargs
        )
    
    if tool in ["projectsteamupdate", "updateprojectteam"]:
        return client.Req_UpdateProjectTeam(
            id=args.get("id"),
            team=args.get("team"),
            changed_by=args.get("changed_by")
        )
        
    if tool in ["projectsstatusupdate", "updateprojectstatus"]:
        return client.Req_UpdateProjectStatus(
            id=args.get("id"),
            status=args.get("status"),
            changed_by=args.get("changed_by")
        )

    # Time
    if tool in ["timelog", "logtime"]:
        return client.Req_LogTimeEntry(
            employee=args.get("employee"),
            project=args.get("project"),
            customer=args.get("customer"),
            date=args.get("date"),
            hours=float(args.get("hours", 0)),
            work_category=args.get("work_category", "dev"),
            notes=args.get("notes", ""),
            billable=bool(args.get("billable", True)),
            status=args.get("status", "draft"),
            logged_by=args.get("logged_by")
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
            employee=args.get("employee"),
            project=args.get("project"),
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
        message = args.get("message") or args.get("text") or args.get("response") or args.get("answer")
        if not message:
            # Fallback if the model put the message in a weird place or just forgot
            message = "No message provided."
        
        # Require outcome to be explicit
        outcome = args.get("outcome")
        if not outcome:
            # Return None to trigger "Action SKIPPED" and force the agent to retry with correct args
            print("⚠ 'outcome' argument missing in 'respond'. Skipping to force LLM retry.")
            return None
            
        return client.Req_ProvideAgentResponse(
            message=str(message),
            outcome=outcome,
            links=args.get("links", [])
        )

    return None
