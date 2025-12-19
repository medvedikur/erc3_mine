"""
Employee tool parsers.
"""
from typing import Any, List, Dict, Union
from erc3.erc3 import client
from erc3.erc3.dtos import SkillLevel, SkillFilter
from ..registry import ToolParser, ParseContext


def _normalize_skill_filters(filters_input: Any) -> List[SkillFilter]:
    """
    Normalize skill/will filters for employees_search.

    Handles various input formats:
    - [{"name": "skill_project_mgmt", "min_level": 7}]
    - [{"name": "project_mgmt", "min_level": 7, "max_level": 10}]
    - {"skill_project_mgmt": 7} -> min_level=7

    Returns List[SkillFilter] for API filtering.
    """
    if not filters_input:
        return []

    result = []

    # Single dict with name -> treat as one filter
    if isinstance(filters_input, dict) and "name" in filters_input:
        filters_input = [filters_input]

    # Dict format: {"skill_project_mgmt": 7} -> min_level shorthand
    if isinstance(filters_input, dict):
        for skill_name, min_level in filters_input.items():
            normalized_name = skill_name.lower().replace(" ", "_")
            # Ensure skill_ or will_ prefix
            if not normalized_name.startswith(("skill_", "will_")):
                normalized_name = f"skill_{normalized_name}"
            result.append(SkillFilter(
                name=normalized_name,
                min_level=int(min_level) if min_level else 1,
                max_level=0  # 0 means no max
            ))
        return result

    # List format
    if isinstance(filters_input, list):
        for item in filters_input:
            if isinstance(item, dict):
                skill_name = item.get("name") or item.get("skill") or item.get("id")
                if skill_name:
                    normalized_name = skill_name.lower().replace(" ", "_")
                    # Ensure skill_ or will_ prefix for skills filters
                    if not normalized_name.startswith(("skill_", "will_")):
                        normalized_name = f"skill_{normalized_name}"
                    result.append(SkillFilter(
                        name=normalized_name,
                        min_level=int(item.get("min_level", item.get("minLevel", 1))),
                        max_level=int(item.get("max_level", item.get("maxLevel", 0)))
                    ))
            elif isinstance(item, SkillFilter):
                result.append(item)

    return result


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
                    # AICODE-NOTE: LLM may generate MongoDB-style operators like {"$add": 1}
                    # We need to handle this gracefully - either convert or reject
                    if isinstance(skill_level, dict):
                        # Handle {"$add": N} operator - means "add N to current level"
                        if "$add" in skill_level:
                            delta = skill_level["$add"]
                            # We cannot apply delta without knowing current level
                            # Return error guidance for correct format
                            raise ValueError(
                                f"Cannot use '$add' operator for skill '{skill_name}'. "
                                f"To update skill level by +{delta}, first use employees_get to find current level, "
                                f"then provide the absolute new level. Example: {{'name': '{skill_name}', 'level': 5}}"
                            )
                        else:
                            # Unknown dict format
                            raise ValueError(
                                f"Invalid level format for skill '{skill_name}': {skill_level}. "
                                f"Level must be an integer 1-10. Example: {{'name': '{skill_name}', 'level': 5}}"
                            )
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
    # AICODE-NOTE: Support both page (1-indexed) and offset (0-indexed) pagination (t009 fix)
    # AICODE-NOTE: offset takes precedence over page when both are provided (t009 regression)
    limit = int(ctx.args.get("limit", ctx.args.get("per_page", 5)))
    explicit_offset = ctx.args.get("offset")
    page = ctx.args.get("page")
    if explicit_offset is not None:
        # Explicit offset always wins
        offset = int(explicit_offset)
    elif page is not None:
        # Convert page to offset: page 1 = offset 0, page 2 = offset 5, etc.
        offset = (int(page) - 1) * limit
    else:
        offset = 0

    return client.Req_ListEmployees(
        offset=offset,
        limit=limit
    )


@ToolParser.register("employees_search", "employeessearch", "searchemployees")
def _parse_employees_search(ctx: ParseContext) -> Any:
    """Search employees by query, location, department, manager, skills, or wills."""
    # Parse skill/will filters if provided
    skills_filter = _normalize_skill_filters(ctx.args.get("skills"))
    wills_filter = _normalize_skill_filters(ctx.args.get("wills"))

    # For wills, ensure will_ prefix instead of skill_
    for wf in wills_filter:
        if wf.name.startswith("skill_"):
            wf.name = "will_" + wf.name[6:]
        elif not wf.name.startswith("will_"):
            wf.name = "will_" + wf.name

    # AICODE-NOTE: Build query from multiple possible sources (t044 fix)
    # Agent may use first_name/last_name separately instead of combined query
    query_val = ctx.args.get("query") or ctx.args.get("name") or ctx.args.get("query_regex")
    if not query_val:
        # Combine first_name and last_name into query if provided
        first_name = ctx.args.get("first_name") or ctx.args.get("firstName")
        last_name = ctx.args.get("last_name") or ctx.args.get("lastName")
        if first_name and last_name:
            query_val = f"{first_name} {last_name}"
        elif first_name:
            query_val = first_name
        elif last_name:
            query_val = last_name

    # AICODE-NOTE: Support both page (1-indexed) and offset (0-indexed) pagination (t009 fix)
    # AICODE-NOTE: offset takes precedence over page when both are provided (t009 regression)
    limit = int(ctx.args.get("limit", ctx.args.get("per_page", 5)))
    explicit_offset = ctx.args.get("offset")
    page = ctx.args.get("page")
    if explicit_offset is not None:
        # Explicit offset always wins
        offset = int(explicit_offset)
    elif page is not None:
        # Convert page to offset: page 1 = offset 0, page 2 = offset 5, etc.
        offset = (int(page) - 1) * limit
    else:
        offset = 0

    return client.Req_SearchEmployees(
        query=query_val,
        location=ctx.args.get("location"),
        department=ctx.args.get("department"),
        manager=ctx.args.get("manager"),
        skills=skills_filter,
        wills=wills_filter,
        offset=offset,
        limit=limit
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

    # AICODE-NOTE: Agent may use "note" (singular) instead of "notes" (t047, t049)
    notes_value = ctx.args.get("notes") or ctx.args.get("note")

    update_args = {
        "employee": ctx.args.get("employee") or ctx.args.get("id") or ctx.args.get("employee_id") or ctx.current_user,
        "notes": notes_value,
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
