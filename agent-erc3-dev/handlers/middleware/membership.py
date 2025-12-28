"""
Project membership middleware for time logging validation.
"""
import re
from erc3.erc3 import client
from ..base import ToolContext, Middleware
from utils import CLI_YELLOW, CLI_RED, CLI_CLR


class ProjectMembershipMiddleware(Middleware):
    """
    Middleware that verifies if an employee is a member of the project
    before allowing a time log entry.
    This prevents the agent from logging time to the wrong project just because
    the name matched partially.

    Also checks for M&A policy compliance (CC codes) if merger.md exists.
    """
    def process(self, ctx: ToolContext) -> None:
        # Intercept Time Logging
        if isinstance(ctx.model, client.Req_LogTimeEntry):
            employee_id = ctx.model.employee
            project_id = ctx.model.project

            # Skip check if arguments are missing (validation will catch it later)
            if not employee_id or not project_id:
                return

            print(f"  {CLI_YELLOW}🛡️ Safety Check: Verifying project membership...{CLI_CLR}")

            # CHECK AUTHORIZATION: Can current user log time for target employee?
            security_manager = ctx.shared.get('security_manager')
            current_user = getattr(security_manager, 'current_user', None) if security_manager else None

            if current_user and employee_id != current_user:
                # Logging time for ANOTHER employee - need authorization
                print(f"  {CLI_YELLOW}🛡️ Authorization Check: Can {current_user} log time for {employee_id}?{CLI_CLR}")

                is_authorized = False
                auth_reason = None

                # AICODE-NOTE: t034 FIX - Only draft entries can be logged for others.
                # Project leads may log DRAFT entries for team members, but cannot approve/submit.
                status = (getattr(ctx.model, 'status', None) or 'draft').lower()
                if status != 'draft':
                    print(f"  {CLI_RED}⛔ Authorization Denied: Non-draft time log for another employee{CLI_CLR}")
                    ctx.stop_execution = True
                    ctx.results.append(
                        f"AUTHORIZATION ERROR: You cannot log time with status '{status}' for another employee. "
                        f"Only **draft** entries can be logged on behalf of others, and approvals must be done by "
                        f"the proper reviewer. Use `denied_security` for this request."
                    )
                    return

                try:
                    # 1. Check if current_user is Direct Manager of target employee
                    target_employee = ctx.api.dispatch(client.Req_GetEmployee(id=employee_id))
                    if target_employee and hasattr(target_employee, 'employee') and target_employee.employee:
                        target_manager = getattr(target_employee.employee, 'manager', None)
                        if target_manager == current_user:
                            is_authorized = True
                            auth_reason = "Direct Manager"

                    # 2. Check if current_user is Project Lead
                    if not is_authorized and project_id:
                        try:
                            resp_project = ctx.api.dispatch(client.Req_GetProject(id=project_id))
                            if resp_project and hasattr(resp_project, 'project') and resp_project.project:
                                for member in (resp_project.project.team or []):
                                    mem_emp = getattr(member, 'employee', None)
                                    mem_role = getattr(member, 'role', None)
                                    if mem_emp == current_user and mem_role == 'Lead':
                                        is_authorized = True
                                        auth_reason = "Project Lead"
                                        break
                        except Exception:
                            pass

                    # 3. Check if current_user is Account Manager for customer
                    # (Would need customer lookup - skip for now as it's less common)

                except Exception as e:
                    print(f"  {CLI_YELLOW}⚠️ Authorization check failed: {e}{CLI_CLR}")
                    # Fail-safe: block if we can't verify
                    is_authorized = False

                if not is_authorized:
                    print(f"  {CLI_RED}⛔ Authorization Denied: Not authorized to log time for {employee_id}{CLI_CLR}")
                    ctx.stop_execution = True
                    ctx.results.append(
                        f"AUTHORIZATION ERROR: You ({current_user}) are NOT authorized to log time for employee '{employee_id}'. "
                        f"To log time for another employee, you must be their:\n"
                        f"  • Direct Manager (verified via employees_get)\n"
                        f"  • Project Lead (role='Lead' in project team)\n"
                        f"  • Account Manager for their customer\n\n"
                        f"Use `denied_security` outcome if you cannot establish authorization."
                    )
                    return
                else:
                    print(f"  {CLI_YELLOW}✓ Authorized as: {auth_reason}{CLI_CLR}")

            # CHECK M&A POLICY: If merger.md exists and requires CC codes, inject a warning
            wiki_manager = ctx.shared.get('wiki_manager')
            if wiki_manager and wiki_manager.has_page("merger.md"):
                merger_content = wiki_manager.get_page("merger.md")
                if merger_content and "cost centre" in merger_content.lower():
                    # M&A policy requires CC codes for time entries
                    # Check if task text mentions CC code or the notes contain one
                    task = ctx.shared.get('task')
                    task_text = (getattr(task, 'task_text', '') or '').lower() if task else ''
                    notes = (ctx.model.notes or '').upper()

                    # CC code format: CC-<Region>-<Unit>-<ProjectCode> e.g. CC-EU-AI-042
                    # Be flexible with format - accept any CC-XXX-XX-XXX pattern (letters/numbers)
                    # User might provide CC-NORD-AI-12O (with letter O instead of 0)
                    cc_pattern = r'CC-[A-Z0-9]{2,4}-[A-Z0-9]{2,4}-[A-Z0-9]{2,4}'
                    has_cc_in_task = bool(re.search(cc_pattern, task_text.upper()))
                    has_cc_in_notes = bool(re.search(cc_pattern, notes))

                    if not has_cc_in_task and not has_cc_in_notes:
                        print(f"  {CLI_RED}⚠️ M&A Policy: CC code required but not provided!{CLI_CLR}")
                        ctx.stop_execution = True
                        ctx.results.append(
                            f"⚠️ M&A POLICY VIOLATION: Per merger.md, all time entries now require a Cost Centre (CC) code. "
                            f"Format: CC-<Region>-<Unit>-<ProjectCode> (e.g., CC-EU-AI-042). "
                            f"You MUST ask the user for the CC code before logging time. "
                            f"Use `none_clarification_needed` to request the CC code from the user."
                        )
                        return

            try:
                # Fetch Project Details
                # We use the API available in context
                # Try positional arg first, then project_id/id kwarg to handle API variations
                try:
                    resp_project = ctx.api.get_project(project_id)
                except TypeError:
                    try:
                        resp_project = ctx.api.get_project(project_id=project_id)
                    except TypeError:
                        resp_project = ctx.api.get_project(id=project_id)

                # Check Membership
                # Resp_GetProject contains 'project' field which has 'team'
                project = getattr(resp_project, 'project', None)
                if not project:
                     # Project not found?
                     print(f"  {CLI_YELLOW}⚠️ Safety Check: Project '{project_id}' not found via API.{CLI_CLR}")
                     # Let the action proceed to fail naturally or handle as error?
                     # If we can't find it, we can't check membership.
                     # But time_log will likely fail if project invalid.
                     return

                # Assuming project.team is a list of employee IDs or objects with 'id'
                is_member = False
                if project.team:
                    # Handle both list of strings and list of objects
                    for member in project.team:
                        if isinstance(member, str):
                            if member == employee_id:
                                is_member = True
                                break
                        elif hasattr(member, 'id') and member.id == employee_id:
                            is_member = True
                            break
                        elif hasattr(member, 'employee_id') and member.employee_id == employee_id:
                            is_member = True
                            break
                        elif hasattr(member, 'employee') and member.employee == employee_id:
                            is_member = True
                            break

                if not is_member:
                    print(f"  {CLI_RED}⛔ Safety Violation: Employee not in project.{CLI_CLR}")
                    ctx.stop_execution = True
                    ctx.results.append(
                        f"SAFETY ERROR: Employee '{employee_id}' is NOT a member of project '{project.name}' ({project_id}). "
                        f"You cannot log time for an employee on a project they are not assigned to. "
                        f"Please verify the project ID. You may need to search for other projects or check project details. "
                        f"Tip: You can use 'time_search' for this employee to see which projects they have worked on recently."
                    )

            except Exception as e:
                # If we can't verify (e.g. project not found or API error),
                # we should probably fail safe or warn.
                # Let's fail safe and block, as logging to non-existent project is also bad.
                print(f"  {CLI_RED}⚠️ Safety Check Failed: {e}{CLI_CLR}")
                # We don't block execution on API error, just warn?
                # Or block? If get_project fails, time_log will likely fail too.
                # Let's let the actual handler try and fail naturally if it's a network error.
                pass
