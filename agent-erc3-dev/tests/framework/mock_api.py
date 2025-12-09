"""
Mock ERC3 API Client.

Intercepts API calls and returns mock responses based on test scenario data.
Tracks all calls for validation and analysis.
"""

from typing import Any, List, Optional, Dict
from dataclasses import dataclass, field
import re

from erc3.erc3 import client, dtos

from .mock_data import MockData, MockWhoAmI, MockEmployee, MockProject, MockCustomer
from .task_builder import TestScenario


@dataclass
class ApiCall:
    """Record of an API call."""
    request_type: str
    request: Any
    response: Any
    error: Optional[Exception] = None


class MockErc3Client:
    """
    Mock ERC3 API client that returns predefined responses.

    This client intercepts all API calls and returns responses from the
    test scenario's mock data. It also tracks all calls for later analysis.
    """

    def __init__(self, scenario: TestScenario):
        self.scenario = scenario
        self.identity = scenario.identity
        self.data = scenario.get_mock_data()
        self.call_log: List[ApiCall] = []
        self._final_response: Optional[Any] = None
        self._task_done = False

        # State tracking for mutations
        self._updated_employees: Dict[str, MockEmployee] = {}
        self._updated_projects: Dict[str, MockProject] = {}
        self._logged_time_entries: List[Any] = []

    @property
    def task_done(self) -> bool:
        """Check if agent has submitted final response."""
        return self._task_done

    @property
    def final_response(self) -> Optional[Any]:
        """Get the final response submitted by agent."""
        return self._final_response

    def dispatch(self, req) -> Any:
        """
        Dispatch request to appropriate mock handler.

        This is the main entry point - all API calls go through here.
        """
        req_type = type(req).__name__

        # Check for configured API errors
        error_key = self._get_error_key(req)
        if error_key and error_key in self.data.api_errors:
            error = self.data.api_errors[error_key]
            self.call_log.append(ApiCall(req_type, req, None, error))
            raise error

        # Check for custom responses
        custom_key = self._get_custom_key(req)
        if custom_key and custom_key in self.data.custom_responses:
            response = self.data.custom_responses[custom_key]
            self.call_log.append(ApiCall(req_type, req, response))
            return response

        # Route to appropriate handler
        try:
            response = self._handle_request(req)
            self.call_log.append(ApiCall(req_type, req, response))
            return response
        except Exception as e:
            self.call_log.append(ApiCall(req_type, req, None, e))
            raise

    def _get_error_key(self, req) -> Optional[str]:
        """Get error lookup key for request."""
        type_name = type(req).__name__
        key_map = {
            "Req_WhoAmI": "whoami",
            "Req_ListEmployees": "employees_list",
            "Req_SearchEmployees": "employees_search",
            "Req_GetEmployee": "employees_get",
            "Req_UpdateEmployeeInfo": "employees_update",
            "Req_ListProjects": "projects_list",
            "Req_SearchProjects": "projects_search",
            "Req_GetProject": "projects_get",
            "Req_UpdateProjectTeam": "projects_team_update",
            "Req_UpdateProjectStatus": "projects_status_update",
            "Req_ListCustomers": "customers_list",
            "Req_SearchCustomers": "customers_search",
            "Req_GetCustomer": "customers_get",
            "Req_ListWiki": "wiki_list",
            "Req_LoadWiki": "wiki_load",
            "Req_SearchWiki": "wiki_search",
            "Req_LogTimeEntry": "time_log",
            "Req_SearchTimeEntries": "time_search",
            "Req_GetTimeEntry": "time_get",
            "Req_UpdateTimeEntry": "time_update",
            "Req_TimeSummaryByEmployee": "time_summary_employee",
            "Req_TimeSummaryByProject": "time_summary_project",
        }
        return key_map.get(type_name)

    def _get_custom_key(self, req) -> Optional[str]:
        """Get custom response lookup key for request."""
        return self._get_error_key(req)

    def _handle_request(self, req) -> Any:
        """Route request to specific handler."""
        handlers = {
            client.Req_WhoAmI: self._handle_whoami,
            client.Req_ListEmployees: self._handle_list_employees,
            client.Req_SearchEmployees: self._handle_search_employees,
            client.Req_GetEmployee: self._handle_get_employee,
            client.Req_UpdateEmployeeInfo: self._handle_update_employee,
            client.Req_ListProjects: self._handle_list_projects,
            client.Req_SearchProjects: self._handle_search_projects,
            client.Req_GetProject: self._handle_get_project,
            client.Req_UpdateProjectTeam: self._handle_update_project_team,
            client.Req_UpdateProjectStatus: self._handle_update_project_status,
            client.Req_ListCustomers: self._handle_list_customers,
            client.Req_SearchCustomers: self._handle_search_customers,
            client.Req_GetCustomer: self._handle_get_customer,
            client.Req_ListWiki: self._handle_list_wiki,
            client.Req_LoadWiki: self._handle_load_wiki,
            client.Req_SearchWiki: self._handle_search_wiki,
            client.Req_LogTimeEntry: self._handle_log_time,
            client.Req_SearchTimeEntries: self._handle_search_time,
            client.Req_GetTimeEntry: self._handle_get_time,
            client.Req_UpdateTimeEntry: self._handle_update_time,
            client.Req_TimeSummaryByEmployee: self._handle_time_summary_by_employee,
            client.Req_TimeSummaryByProject: self._handle_time_summary_by_project,
            client.Req_ProvideAgentResponse: self._handle_respond,
        }

        req_type = type(req)
        handler = handlers.get(req_type)

        if handler:
            return handler(req)

        # Also check by class name for patched classes
        for base_type, handler in handlers.items():
            if base_type.__name__ in type(req).__name__ or issubclass(type(req), base_type):
                return handler(req)

        raise ValueError(f"Unsupported request type: {type(req).__name__}")

    # =========================================================================
    # Identity
    # =========================================================================

    def _handle_whoami(self, req) -> dtos.Resp_WhoAmI:
        """Handle /whoami request."""
        return dtos.Resp_WhoAmI(
            is_public=self.identity.is_public,
            current_user=self.identity.user,  # MockWhoAmI.user -> current_user
            department=self.identity.department,
            location=self.identity.location,
            today=self.identity.today,
            wiki_sha1=self.identity.wiki_hash,  # MockWhoAmI.wiki_hash -> wiki_sha1
        )

    # =========================================================================
    # Employees
    # =========================================================================

    def _handle_list_employees(self, req) -> dtos.Resp_ListEmployees:
        """Handle /employees/list request."""
        offset = getattr(req, 'offset', 0)
        limit = getattr(req, 'limit', 5)

        employees = self.data.employees[offset:offset + limit]
        next_offset = offset + limit if offset + limit < len(self.data.employees) else -1

        return dtos.Resp_ListEmployees(
            employees=[self._to_employee_dto(e) for e in employees],
            next_offset=next_offset,
        )

    def _handle_search_employees(self, req) -> dtos.Resp_SearchEmployees:
        """Handle /employees/search request."""
        query = getattr(req, 'query', None)
        location = getattr(req, 'location', None)
        department = getattr(req, 'department', None)
        manager = getattr(req, 'manager', None)
        offset = getattr(req, 'offset', 0)
        limit = getattr(req, 'limit', 5)

        results = self.data.search_employees(query, location, department, manager)
        paged = results[offset:offset + limit]
        next_offset = offset + limit if offset + limit < len(results) else -1

        return dtos.Resp_SearchEmployees(
            employees=[self._to_employee_dto(e) for e in paged],
            next_offset=next_offset,
        )

    def _handle_get_employee(self, req) -> dtos.Resp_GetEmployee:
        """Handle /employees/get request."""
        emp_id = getattr(req, 'id', None)

        # Check if employee was updated
        if emp_id in self._updated_employees:
            emp = self._updated_employees[emp_id]
        else:
            emp = self.data.get_employee(emp_id)

        if not emp:
            raise Exception(f"Employee not found: {emp_id}")

        return dtos.Resp_GetEmployee(employee=self._to_employee_view_dto(emp))

    def _handle_update_employee(self, req) -> dtos.Resp_UpdateEmployeeInfo:
        """Handle /employees/update request."""
        emp_id = getattr(req, 'employee', None)
        emp = self.data.get_employee(emp_id)

        if not emp:
            raise Exception(f"Employee not found: {emp_id}")

        # Apply updates
        if hasattr(req, 'salary') and req.salary is not None:
            emp.salary = req.salary
        if hasattr(req, 'notes') and req.notes is not None:
            emp.notes = req.notes
        if hasattr(req, 'location') and req.location is not None:
            emp.location = req.location
        if hasattr(req, 'department') and req.department is not None:
            emp.department = req.department
        if hasattr(req, 'skills') and req.skills:
            emp.skills = req.skills
        if hasattr(req, 'wills') and req.wills:
            emp.wills = req.wills

        self._updated_employees[emp_id] = emp

        return dtos.Resp_UpdateEmployeeInfo(success=True)

    def _to_employee_dto(self, emp: MockEmployee) -> dtos.EmployeeBrief:
        """Convert MockEmployee to DTO (brief version for search/list)."""
        return dtos.EmployeeBrief(
            id=emp.id,
            name=emp.name,
            email=emp.email,
            salary=emp.salary,
            location=emp.location,
            department=emp.department,
        )

    def _to_employee_view_dto(self, emp: MockEmployee) -> dtos.EmployeeView:
        """Convert MockEmployee to full DTO (for get)."""
        return dtos.EmployeeView(
            id=emp.id,
            name=emp.name,
            email=emp.email,
            salary=emp.salary,
            location=emp.location,
            department=emp.department,
            notes=emp.notes,
            skills=emp.skills,
            wills=emp.wills,
        )

    # =========================================================================
    # Projects
    # =========================================================================

    def _handle_list_projects(self, req) -> dtos.Resp_ListProjects:
        """Handle /projects/list request."""
        offset = getattr(req, 'offset', 0)
        limit = getattr(req, 'limit', 5)

        projects = self.data.projects[offset:offset + limit]
        next_offset = offset + limit if offset + limit < len(self.data.projects) else -1

        return dtos.Resp_ListProjects(
            projects=[self._to_project_summary_dto(p) for p in projects],
            next_offset=next_offset,
        )

    def _handle_search_projects(self, req) -> dtos.Resp_ProjectSearchResults:
        """Handle /projects/search request."""
        query = getattr(req, 'query', None)
        customer_id = getattr(req, 'customer_id', None)
        status = getattr(req, 'status', None)
        team_filter = getattr(req, 'team', None)
        offset = getattr(req, 'offset', 0)
        limit = getattr(req, 'limit', 5)

        # Get member from team filter if present
        member = None
        if team_filter and hasattr(team_filter, 'employee_id'):
            member = team_filter.employee_id

        results = self.data.search_projects(query, customer_id, status, member)
        paged = results[offset:offset + limit]
        next_offset = offset + limit if offset + limit < len(results) else -1

        return dtos.Resp_ProjectSearchResults(
            projects=[self._to_project_summary_dto(p) for p in paged],
            next_offset=next_offset,
        )

    def _handle_get_project(self, req) -> dtos.Resp_GetProject:
        """Handle /projects/get request."""
        proj_id = getattr(req, 'id', None)

        # Check if project was updated
        if proj_id in self._updated_projects:
            proj = self._updated_projects[proj_id]
        else:
            proj = self.data.get_project(proj_id)

        if not proj:
            return dtos.Resp_GetProject(project=None, found=False)

        return dtos.Resp_GetProject(project=self._to_project_dto(proj), found=True)

    def _handle_update_project_team(self, req) -> dtos.Resp_UpdateProjectTeam:
        """Handle /projects/team/update request."""
        proj_id = getattr(req, 'id', None)
        proj = self.data.get_project(proj_id)

        if not proj:
            raise Exception(f"Project not found: {proj_id}")

        # Update team
        team_data = getattr(req, 'team', [])
        from .mock_data import MockTeamMember
        proj.team = [
            MockTeamMember(
                employee=m.get('employee') if isinstance(m, dict) else getattr(m, 'employee', ''),
                role=m.get('role', 'Other') if isinstance(m, dict) else getattr(m, 'role', 'Other'),
                time_slice=m.get('time_slice', 0.0) if isinstance(m, dict) else getattr(m, 'time_slice', 0.0),
            )
            for m in team_data
        ]

        self._updated_projects[proj_id] = proj

        return dtos.Resp_UpdateProjectTeam(success=True)

    def _handle_update_project_status(self, req) -> dtos.Resp_UpdateProjectStatus:
        """Handle /projects/status/update request."""
        proj_id = getattr(req, 'id', None)
        new_status = getattr(req, 'status', None)
        proj = self.data.get_project(proj_id)

        if not proj:
            raise Exception(f"Project not found: {proj_id}")

        proj.status = new_status
        self._updated_projects[proj_id] = proj

        return dtos.Resp_UpdateProjectStatus(success=True)

    def _to_project_summary_dto(self, proj: MockProject) -> dtos.ProjectBrief:
        """Convert MockProject to summary DTO."""
        return dtos.ProjectBrief(
            id=proj.id,
            name=proj.name,
            customer=proj.customer,
            status=proj.status,
        )

    def _to_project_dto(self, proj: MockProject) -> dtos.ProjectDetail:
        """Convert MockProject to full DTO."""
        return dtos.ProjectDetail(
            id=proj.id,
            name=proj.name,
            customer=proj.customer,
            status=proj.status,
            description=proj.description,
            team=[
                dtos.Workload(
                    employee=m.employee,
                    role=m.role,
                    time_slice=m.time_slice,
                )
                for m in proj.team
            ],
        )

    # =========================================================================
    # Customers
    # =========================================================================

    def _handle_list_customers(self, req) -> dtos.Resp_ListCustomers:
        """Handle /customers/list request."""
        offset = getattr(req, 'offset', 0)
        limit = getattr(req, 'limit', 5)

        customers = self.data.customers[offset:offset + limit]
        next_offset = offset + limit if offset + limit < len(self.data.customers) else -1

        return dtos.Resp_ListCustomers(
            companies=[self._to_company_brief_dto(c) for c in customers],
            next_offset=next_offset,
        )

    def _handle_search_customers(self, req) -> dtos.Resp_CustomerSearchResults:
        """Handle /customers/search request."""
        query = getattr(req, 'query', None)
        locations = getattr(req, 'locations', None)
        deal_phase = getattr(req, 'deal_phase', None)
        account_managers = getattr(req, 'account_managers', None)
        offset = getattr(req, 'offset', 0)
        limit = getattr(req, 'limit', 5)

        results = self.data.search_customers(query, locations, deal_phase, account_managers)
        paged = results[offset:offset + limit]
        next_offset = offset + limit if offset + limit < len(results) else -1

        return dtos.Resp_CustomerSearchResults(
            companies=[self._to_company_brief_dto(c) for c in paged],
            next_offset=next_offset,
        )

    def _handle_get_customer(self, req) -> dtos.Resp_GetCustomer:
        """Handle /customers/get request."""
        cust_id = getattr(req, 'id', None)
        cust = self.data.get_customer(cust_id)

        if not cust:
            return dtos.Resp_GetCustomer(company=None, found=False)

        return dtos.Resp_GetCustomer(company=self._to_company_detail_dto(cust), found=True)

    def _to_company_brief_dto(self, cust: MockCustomer) -> dtos.CompanyBrief:
        """Convert MockCustomer to brief DTO (for list/search)."""
        return dtos.CompanyBrief(
            id=cust.id,
            name=cust.name,
            location=cust.location,
            deal_phase=cust.deal_phase,
            high_level_status=cust.high_level_status if hasattr(cust, 'high_level_status') else "active",
        )

    def _to_company_detail_dto(self, cust: MockCustomer) -> dtos.CompanyDetail:
        """Convert MockCustomer to full DTO (for get)."""
        return dtos.CompanyDetail(
            id=cust.id,
            name=cust.name,
            brief=cust.notes or "",
            location=cust.location,
            primary_contact_name=getattr(cust, 'primary_contact_name', ''),
            primary_contact_email=getattr(cust, 'primary_contact_email', ''),
            deal_phase=cust.deal_phase,
            high_level_status=getattr(cust, 'high_level_status', 'active'),
            account_manager=cust.account_manager,
        )

    # =========================================================================
    # Wiki
    # =========================================================================

    def _handle_list_wiki(self, req) -> dtos.Resp_ListWiki:
        """Handle /wiki/list request."""
        # Return standard wiki file list
        paths = [
            "README.md", "rulebook.md", "hierarchy.md", "background.md",
            "culture.md", "mission_vision.md", "skills.md", "systems.md",
            "offices_index.md", "offices_amsterdam.md", "offices_munich.md", "offices_vienna.md",
            "people_index.md", "people_elena_vogel.md", "people_richard_klein.md",
            "people_lukas_brenner.md", "people_felix_baum.md", "people_jonas_weiss.md",
            "people_helene_stutz.md", "people_klara_houtman.md", "people_ana_kovac.md",
            "people_marko_petrovic.md", "people_sofia_rinaldi.md", "people_timo_van_dijk.md",
            "people_mira_schaefer.md",
        ]

        # Post-merger wiki version includes merger.md
        wiki_hash = self.identity.wiki_hash or "test_wiki_hash"
        if wiki_hash.startswith("a744c2c0"):
            paths.append("merger.md")

        # Post-Tempo wiki version includes merger.md AND tempo_migration.md
        if wiki_hash.startswith("b8f5d3a0"):
            paths.append("merger.md")
            paths.append("tempo_migration.md")

        return dtos.Resp_ListWiki(paths=paths, sha1=wiki_hash)

    def _handle_load_wiki(self, req) -> dtos.Resp_LoadWiki:
        """Handle /wiki/load request."""
        file_name = getattr(req, 'file', '')

        # Check custom responses first
        if f"wiki_load:{file_name}" in self.data.custom_responses:
            content = self.data.custom_responses[f"wiki_load:{file_name}"]
            return dtos.Resp_LoadWiki(file=file_name, content=content)

        # Default: return placeholder content
        # In real tests, we'll load from wiki_dump_tests/
        content = f"# {file_name}\n\nMock wiki content for {file_name}"

        return dtos.Resp_LoadWiki(file=file_name, content=content)

    def _handle_search_wiki(self, req) -> dtos.Resp_SearchWiki:
        """Handle /wiki/search request."""
        query = getattr(req, 'query_regex', '')

        # Return mock search results
        # In real implementation, WikiManager handles this locally
        return dtos.Resp_SearchWiki(
            results=[
                dtos.SearchSnippet(
                    path="rulebook.md",
                    linum=10,
                    content=f"Mock match for '{query}'",
                )
            ]
        )

    # =========================================================================
    # Time Tracking
    # =========================================================================

    def _handle_log_time(self, req) -> dtos.Resp_LogTimeEntry:
        """Handle /time/log request."""
        employee = getattr(req, 'employee', None)
        project = getattr(req, 'project', None)
        customer = getattr(req, 'customer', None)
        date = getattr(req, 'date', None)
        hours = getattr(req, 'hours', 0)
        work_category = getattr(req, 'work_category', 'dev')
        notes = getattr(req, 'notes', '') or ''
        billable = getattr(req, 'billable', True)
        status = getattr(req, 'status', 'draft')

        entry_id = f"time_{len(self._logged_time_entries) + 1}"

        entry = {
            "id": entry_id,
            "employee": employee,
            "project": project,
            "customer": customer,
            "date": date,
            "hours": hours,
            "work_category": work_category,
            "notes": notes,
            "billable": billable,
            "status": status,
        }
        self._logged_time_entries.append(entry)

        # Resp_LogTimeEntry inherits from TimeEntryWithID which requires all TimeEntry fields
        return dtos.Resp_LogTimeEntry(
            id=entry_id,
            employee=employee,
            project=project,
            customer=customer,
            date=date,
            hours=hours,
            work_category=work_category,
            notes=notes,
            billable=billable,
            status=status,
        )

    def _handle_search_time(self, req) -> dtos.Resp_SearchTimeEntries:
        """Handle /time/search request."""
        employee = getattr(req, 'employee', None)
        project = getattr(req, 'project', None)
        date_from = getattr(req, 'date_from', None)
        date_to = getattr(req, 'date_to', None)
        offset = getattr(req, 'offset', 0)
        limit = getattr(req, 'limit', 5)

        # Combine mock data entries + logged entries
        all_entries = list(self.data.time_entries)
        for logged in self._logged_time_entries:
            from .mock_data import MockTimeEntry
            all_entries.append(MockTimeEntry(**logged))

        # Filter
        results = []
        for entry in all_entries:
            if employee and entry.employee != employee:
                continue
            if project and entry.project != project:
                continue
            if date_from and entry.date < date_from:
                continue
            if date_to and entry.date > date_to:
                continue
            results.append(entry)

        paged = results[offset:offset + limit]
        next_offset = offset + limit if offset + limit < len(results) else -1

        # Calculate totals
        total_hours = sum(e.hours for e in results)
        total_billable = sum(e.hours for e in results if e.billable)
        total_non_billable = sum(e.hours for e in results if not e.billable)

        return dtos.Resp_SearchTimeEntries(
            entries=[self._to_time_entry_with_id_dto(e) for e in paged],
            next_offset=next_offset,
            total_hours=total_hours,
            total_billable=total_billable,
            total_non_billable=total_non_billable,
        )

    def _handle_get_time(self, req) -> dtos.Resp_GetTimeEntry:
        """Handle /time/get request."""
        entry_id = getattr(req, 'id', None)

        # Search in mock data
        for entry in self.data.time_entries:
            if entry.id == entry_id:
                return dtos.Resp_GetTimeEntry(entry=self._to_time_entry_dto(entry), found=True)

        # Search in logged entries
        for logged in self._logged_time_entries:
            if logged.get('id') == entry_id:
                from .mock_data import MockTimeEntry
                entry = MockTimeEntry(**logged)
                return dtos.Resp_GetTimeEntry(entry=self._to_time_entry_dto(entry), found=True)

        return dtos.Resp_GetTimeEntry(entry=None, found=False)

    def _handle_update_time(self, req) -> dtos.Resp_TimeEntryUpdated:
        """Handle /time/update request."""
        entry_id = getattr(req, 'id', None)

        # Find and update entry in logged entries
        for logged in self._logged_time_entries:
            if logged.get('id') == entry_id:
                if hasattr(req, 'hours') and req.hours is not None:
                    logged['hours'] = req.hours
                if hasattr(req, 'notes') and req.notes is not None:
                    logged['notes'] = req.notes
                if hasattr(req, 'date') and req.date is not None:
                    logged['date'] = req.date
                if hasattr(req, 'billable') and req.billable is not None:
                    logged['billable'] = req.billable
                if hasattr(req, 'status') and req.status is not None:
                    logged['status'] = req.status
                return dtos.Resp_TimeEntryUpdated()

        # Also check base time entries
        for entry in self.data.time_entries:
            if entry.id == entry_id:
                if hasattr(req, 'hours') and req.hours is not None:
                    entry.hours = req.hours
                if hasattr(req, 'notes') and req.notes is not None:
                    entry.notes = req.notes
                return dtos.Resp_TimeEntryUpdated()

        raise Exception(f"Time entry not found: {entry_id}")

    def _handle_time_summary_by_employee(self, req) -> dtos.Resp_TimeSummaryByEmployee:
        """Handle /time/summary/by-employee request."""
        employees_filter = getattr(req, 'employees', [])
        projects_filter = getattr(req, 'projects', [])
        date_from = getattr(req, 'date_from', None)
        date_to = getattr(req, 'date_to', None)
        billable_filter = getattr(req, 'billable', '')

        # Combine all entries
        all_entries = list(self.data.time_entries)
        for logged in self._logged_time_entries:
            from .mock_data import MockTimeEntry
            all_entries.append(MockTimeEntry(**logged))

        # Aggregate by employee
        summary = {}
        for entry in all_entries:
            # Apply filters
            if employees_filter and entry.employee not in employees_filter:
                continue
            if projects_filter and entry.project not in projects_filter:
                continue
            if date_from and entry.date < date_from:
                continue
            if date_to and entry.date > date_to:
                continue
            if billable_filter == 'billable' and not entry.billable:
                continue
            if billable_filter == 'non_billable' and entry.billable:
                continue

            emp = entry.employee
            if emp not in summary:
                summary[emp] = {'employee': emp, 'total_hours': 0, 'billable_hours': 0, 'non_billable_hours': 0}
            summary[emp]['total_hours'] += entry.hours
            if entry.billable:
                summary[emp]['billable_hours'] += entry.hours
            else:
                summary[emp]['non_billable_hours'] += entry.hours

        return dtos.Resp_TimeSummaryByEmployee(
            summaries=[
                dtos.TimeSummaryByEmployee(
                    employee=s['employee'],
                    total_hours=s['total_hours'],
                    billable_hours=s['billable_hours'],
                    non_billable_hours=s['non_billable_hours'],
                )
                for s in summary.values()
            ]
        )

    def _handle_time_summary_by_project(self, req) -> dtos.Resp_TimeSummaryByProject:
        """Handle /time/summary/by-project request."""
        employees_filter = getattr(req, 'employees', [])
        projects_filter = getattr(req, 'projects', [])
        date_from = getattr(req, 'date_from', None)
        date_to = getattr(req, 'date_to', None)
        billable_filter = getattr(req, 'billable', '')

        # Combine all entries
        all_entries = list(self.data.time_entries)
        for logged in self._logged_time_entries:
            from .mock_data import MockTimeEntry
            all_entries.append(MockTimeEntry(**logged))

        # Aggregate by project
        summary = {}
        for entry in all_entries:
            if not entry.project:
                continue
            # Apply filters
            if employees_filter and entry.employee not in employees_filter:
                continue
            if projects_filter and entry.project not in projects_filter:
                continue
            if date_from and entry.date < date_from:
                continue
            if date_to and entry.date > date_to:
                continue
            if billable_filter == 'billable' and not entry.billable:
                continue
            if billable_filter == 'non_billable' and entry.billable:
                continue

            proj = entry.project
            if proj not in summary:
                summary[proj] = {
                    'project': proj,
                    'customer': entry.customer or '',
                    'total_hours': 0,
                    'billable_hours': 0,
                    'non_billable_hours': 0,
                    'employees': set()
                }
            summary[proj]['total_hours'] += entry.hours
            summary[proj]['employees'].add(entry.employee)
            if entry.billable:
                summary[proj]['billable_hours'] += entry.hours
            else:
                summary[proj]['non_billable_hours'] += entry.hours

        return dtos.Resp_TimeSummaryByProject(
            summaries=[
                dtos.TimeSummaryByProject(
                    project=s['project'],
                    customer=s['customer'],
                    total_hours=s['total_hours'],
                    billable_hours=s['billable_hours'],
                    non_billable_hours=s['non_billable_hours'],
                    distinct_employees=len(s['employees']),
                )
                for s in summary.values()
            ]
        )

    def _to_time_entry_dto(self, entry) -> dtos.TimeEntry:
        """Convert MockTimeEntry to DTO (without ID)."""
        return dtos.TimeEntry(
            employee=entry.employee,
            project=entry.project,
            customer=entry.customer,
            date=entry.date,
            hours=entry.hours,
            work_category=entry.work_category,
            notes=entry.notes or "",
            billable=entry.billable,
            status=entry.status or "draft",
        )

    def _to_time_entry_with_id_dto(self, entry) -> dtos.TimeEntryWithID:
        """Convert MockTimeEntry to DTO with ID (for search results)."""
        return dtos.TimeEntryWithID(
            id=entry.id,
            employee=entry.employee,
            project=entry.project,
            customer=entry.customer,
            date=entry.date,
            hours=entry.hours,
            work_category=entry.work_category,
            notes=entry.notes or "",
            billable=entry.billable,
            status=entry.status or "draft",
        )

    # =========================================================================
    # Agent Response
    # =========================================================================

    def _handle_respond(self, req) -> dtos.Resp_ProvideAgentResponse:
        """Handle /respond request - marks task as done."""
        self._final_response = {
            "outcome": getattr(req, 'outcome', 'ok_answer'),
            "message": getattr(req, 'message', ''),
            "links": getattr(req, 'links', []),
        }
        self._task_done = True

        return dtos.Resp_ProvideAgentResponse(success=True)

    # =========================================================================
    # API Client Interface (for compatibility with real Erc3Client)
    # =========================================================================

    def get_erc_dev_client(self, task) -> 'MockErc3Client':
        """Return self as the ERC dev client - mock implements the same interface."""
        return self

    def list_wiki(self) -> dtos.Resp_ListWiki:
        """Direct method for WikiManager compatibility."""
        return self._handle_list_wiki(None)

    def load_wiki(self, path: str) -> dtos.Resp_LoadWiki:
        """Direct method for WikiManager compatibility."""
        class MockReq:
            def __init__(self, file):
                self.file = file
        return self._handle_load_wiki(MockReq(path))

    def get_project(self, project_id: str = None, id: str = None) -> dtos.Resp_GetProject:
        """Direct method for middleware compatibility."""
        pid = project_id or id
        class MockReq:
            def __init__(self, proj_id):
                self.id = proj_id
        return self._handle_get_project(MockReq(pid))

    def get_employee(self, employee_id: str = None, id: str = None) -> dtos.Resp_GetEmployee:
        """Direct method for middleware compatibility."""
        eid = employee_id or id
        class MockReq:
            def __init__(self, emp_id):
                self.id = emp_id
        return self._handle_get_employee(MockReq(eid))

    def log_llm(
        self,
        task_id: str,
        completion: str,
        *,
        prompt: Any = None,
        model: str = None,
        duration_sec: float = None,
        prompt_tokens: int = None,
        cached_prompt_tokens: int = None,
        completion_tokens: int = None
    ) -> None:
        """Log LLM usage - no-op for tests (SDK 1.2.0+ signature)."""
        pass  # We don't need to log to the benchmark API during tests

    # =========================================================================
    # Convenience methods for analysis
    # =========================================================================

    def get_calls_by_type(self, req_type: str) -> List[ApiCall]:
        """Get all calls of a specific type."""
        return [c for c in self.call_log if c.request_type == req_type]

    def was_called(self, req_type: str) -> bool:
        """Check if a specific request type was called."""
        return any(c.request_type == req_type for c in self.call_log)

    def get_call_count(self, req_type: str) -> int:
        """Get number of calls of a specific type."""
        return len(self.get_calls_by_type(req_type))
