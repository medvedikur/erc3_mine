"""
Employee tool parsers.
"""
from typing import Any
from erc3.erc3 import client
from ..registry import ToolParser, ParseContext
from ..patches import SafeReq_UpdateEmployeeInfo


@ToolParser.register("employees_list", "employeeslist", "listemployees")
def _parse_employees_list(ctx: ParseContext) -> Any:
    """List all employees with pagination."""
    return client.Req_ListEmployees(
        offset=int(ctx.args.get("offset", 0)),
        limit=int(ctx.args.get("limit", 5))
    )


@ToolParser.register("employees_search", "employeessearch", "searchemployees")
def _parse_employees_search(ctx: ParseContext) -> Any:
    """Search employees by query, location, department, or manager."""
    return client.Req_SearchEmployees(
        query=ctx.args.get("query") or ctx.args.get("name") or ctx.args.get("query_regex"),
        location=ctx.args.get("location"),
        department=ctx.args.get("department"),
        manager=ctx.args.get("manager"),
        offset=int(ctx.args.get("offset", 0)),
        limit=int(ctx.args.get("limit", 5))
    )


@ToolParser.register("employees_get", "employeesget", "getemployee")
def _parse_employees_get(ctx: ParseContext) -> Any:
    """Get employee by ID. Falls back to search if only name provided."""
    emp_id = ctx.args.get("id") or ctx.args.get("employee_id") or ctx.args.get("employee")
    username = ctx.args.get("username")

    # Smart dispatch: if ID missing but username/name provided, use search
    if not emp_id and (username or ctx.args.get("name")):
        query = username or ctx.args.get("name")
        return client.Req_SearchEmployees(
            query=query,
            offset=int(ctx.args.get("offset", 0)),
            limit=int(ctx.args.get("limit", 5))
        )

    if not emp_id:
        if ctx.current_user:
            emp_id = ctx.current_user
        else:
            return None

    return client.Req_GetEmployee(id=emp_id)


@ToolParser.register("employees_update", "employeesupdate", "updateemployee",
                     "salary_update", "salaryupdate", "updatesalary")
def _parse_employees_update(ctx: ParseContext) -> Any:
    """Update employee info (salary, notes, location, etc.)."""
    update_args = {
        "employee": ctx.args.get("employee") or ctx.args.get("id") or ctx.args.get("employee_id") or ctx.current_user,
        "notes": ctx.args.get("notes"),
        "salary": ctx.args.get("salary"),
        "location": ctx.args.get("location"),
        "department": ctx.args.get("department"),
        "skills": ctx.args.get("skills"),
        "wills": ctx.args.get("wills"),
        "changed_by": ctx.args.get("changed_by")
    }
    valid_args = {k: v for k, v in update_args.items() if v is not None}
    return SafeReq_UpdateEmployeeInfo(**valid_args)
