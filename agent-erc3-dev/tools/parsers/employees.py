"""
Employee tool parsers.
"""
from typing import Any, List, Dict, Union
from erc3.erc3 import client
from erc3.erc3.dtos import SkillLevel
from ..registry import ToolParser, ParseContext


def _normalize_skills(skills_input: Any) -> List[SkillLevel]:
    """
    Normalize skills input to List[SkillLevel] format.

    Handles various input formats:
    - ["python", "ml"] -> [SkillLevel(name="python", level=3), ...]
    - [{"name": "python", "level": 4}] -> [SkillLevel(...)]
    - [{"skill": "python", "level": 4}] -> [SkillLevel(...)]
    - {"python": 4, "ml": 3} -> [SkillLevel(name="python", level=4), ...]
    - "python" -> [SkillLevel(name="python", level=3)]

    Default level is 3 (intermediate) when not specified.
    """
    if not skills_input:
        return []

    result = []
    DEFAULT_LEVEL = 3

    # Single string -> convert to list
    if isinstance(skills_input, str):
        skills_input = [skills_input]

    # Dict format: {"python": 4, "ml": 3}
    if isinstance(skills_input, dict):
        for skill_name, level in skills_input.items():
            if isinstance(level, int):
                result.append(SkillLevel(name=skill_name.lower().replace(" ", "_"), level=level))
            else:
                result.append(SkillLevel(name=skill_name.lower().replace(" ", "_"), level=DEFAULT_LEVEL))
        return result

    # List format
    if isinstance(skills_input, list):
        for item in skills_input:
            if isinstance(item, str):
                # String in list: ["python", "ml"]
                result.append(SkillLevel(name=item.lower().replace(" ", "_"), level=DEFAULT_LEVEL))
            elif isinstance(item, dict):
                # Dict in list: [{"name": "python", "level": 4}]
                skill_name = item.get("name") or item.get("skill") or item.get("id")
                skill_level = item.get("level", DEFAULT_LEVEL)
                if skill_name:
                    result.append(SkillLevel(
                        name=skill_name.lower().replace(" ", "_"),
                        level=int(skill_level) if skill_level else DEFAULT_LEVEL
                    ))
            elif isinstance(item, SkillLevel):
                result.append(item)

    return result


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
    # Normalize skills and wills to SkillLevel format
    skills_raw = ctx.args.get("skills")
    wills_raw = ctx.args.get("wills")

    skills = _normalize_skills(skills_raw) if skills_raw else None
    wills = _normalize_skills(wills_raw) if wills_raw else None

    update_args = {
        "employee": ctx.args.get("employee") or ctx.args.get("id") or ctx.args.get("employee_id") or ctx.current_user,
        "notes": ctx.args.get("notes"),
        "salary": ctx.args.get("salary"),
        "location": ctx.args.get("location"),
        "department": ctx.args.get("department"),
        "skills": skills,
        "wills": wills,
        "changed_by": ctx.args.get("changed_by")
    }
    valid_args = {k: v for k, v in update_args.items() if v is not None}

    # Use model_construct to bypass strict validation, let API validate
    return client.Req_UpdateEmployeeInfo.model_construct(**valid_args)
