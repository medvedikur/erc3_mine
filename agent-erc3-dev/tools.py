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
    args = action_dict.get("args", {})
    
    # Common mappings
    # Employees
    if tool in ["whoami", "me", "identity"]:
        return client.Req_WhoAmI()
    
    if tool in ["employeeslist", "listemployees"]:
        return client.Req_ListEmployees(
            offset=int(args.get("offset", 0)),
            limit=int(args.get("limit", 10))
        )
    
    if tool in ["employeessearch", "searchemployees"]:
        return client.Req_SearchEmployees(
            query=args.get("query"),
            location=args.get("location"),
            department=args.get("department"),
            manager=args.get("manager"),
            offset=int(args.get("offset", 0)),
            limit=int(args.get("limit", 10))
        )
    
    if tool in ["employeesget", "getemployee"]:
        return client.Req_GetEmployee(id=args.get("id") or args.get("employee_id"))
    
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
        return client.Req_LoadWiki(file=args.get("file") or args.get("path"))
    
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
        return client.Req_ListCustomers(
            offset=int(args.get("offset", 0)),
            limit=int(args.get("limit", 10))
        )
    
    if tool in ["customersget", "getcustomer"]:
        return client.Req_GetCustomer(id=args.get("id"))
    
    if tool in ["customerssearch", "searchcustomers"]:
        return client.Req_SearchCustomers(
            query=args.get("query"),
            deal_phase=args.get("deal_phase"),
            locations=args.get("locations"),
            account_managers=args.get("account_managers"),
            offset=int(args.get("offset", 0)),
            limit=int(args.get("limit", 10))
        )

    # Projects
    if tool in ["projectslist", "listprojects"]:
        return client.Req_ListProjects(
            offset=int(args.get("offset", 0)),
            limit=int(args.get("limit", 10))
        )
    
    if tool in ["projectsget", "getproject"]:
        return client.Req_GetProject(id=args.get("id"))
    
    if tool in ["projectssearch", "searchprojects"]:
        status_arg = args.get("status")
        if isinstance(status_arg, str):
            status = [status_arg]
        elif isinstance(status_arg, list):
            status = status_arg
        else:
            status = []

        return client.Req_SearchProjects(
            query=args.get("query"),
            customer_id=args.get("customer_id"),
            status=status,
            include_archived=bool(args.get("include_archived", False)),
            offset=int(args.get("offset", 0)),
            limit=int(args.get("limit", 10))
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
        return client.Req_GetTimeEntry(id=args.get("id"))

    if tool in ["timesearch", "searchtime"]:
        return client.Req_SearchTimeEntries(
            employee=args.get("employee"),
            project=args.get("project"),
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            billable=args.get("billable", ""),
            offset=int(args.get("offset", 0)),
            limit=int(args.get("limit", 10))
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
            
        return client.Req_ProvideAgentResponse(
            message=str(message),
            outcome=args.get("outcome", "ok_answer"),
            links=args.get("links", [])
        )

    return None

