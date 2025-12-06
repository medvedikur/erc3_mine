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
            user=self.identity.user,
            name=self.identity.name,
            email=self.identity.email,
            department=self.identity.department,
            location=self.identity.location,
            today=self.identity.today,
            wiki_hash=self.identity.wiki_hash,
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
            raise Exception(f"Project not found: {proj_id}")

        return dtos.Resp_GetProject(project=self._to_project_dto(proj))

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
        files = [
            "README.md", "rulebook.md", "hierarchy.md", "background.md",
            "culture.md", "mission_vision.md", "skills.md", "systems.md",
            "offices_index.md", "offices_amsterdam.md", "offices_munich.md", "offices_vienna.md",
            "people_index.md", "people_elena_vogel.md", "people_richard_klein.md",
            "people_lukas_brenner.md", "people_felix_baum.md", "people_jonas_weiss.md",
            "people_helene_stutz.md", "people_klara_houtman.md", "people_ana_kovac.md",
            "people_marko_petrovic.md", "people_sofia_rinaldi.md", "people_timo_van_dijk.md",
            "people_mira_schaefer.md",
        ]
        return dtos.Resp_ListWiki(files=files)

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
        entry = {
            "employee": getattr(req, 'employee', None),
            "project": getattr(req, 'project', None),
            "customer": getattr(req, 'customer', None),
            "date": getattr(req, 'date', None),
            "hours": getattr(req, 'hours', 0),
            "work_category": getattr(req, 'work_category', 'dev'),
            "notes": getattr(req, 'notes', ''),
            "billable": getattr(req, 'billable', True),
            "status": getattr(req, 'status', 'draft'),
        }
        entry_id = f"time_{len(self._logged_time_entries) + 1}"
        self._logged_time_entries.append({"id": entry_id, **entry})

        return dtos.Resp_LogTimeEntry(success=True, id=entry_id)

    def _handle_search_time(self, req) -> dtos.Resp_SearchTimeEntries:
        """Handle /time/search request."""
        # Return logged entries + mock data entries
        entries = self.data.time_entries + [
            type('MockEntry', (), e) for e in self._logged_time_entries
        ]
        return dtos.Resp_SearchTimeEntries(
            entries=[],  # Simplified
            next_offset=-1,
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

    def log_llm(self, task_id: str, model: str, duration_sec: float, usage: Any) -> None:
        """Log LLM usage - no-op for tests."""
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
