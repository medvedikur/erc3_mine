"""
Mock data for testing - represents the fake company state.

Contains base employees, projects, customers that mirror the real benchmark data.
Tests can override specific values using MockDataBuilder.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from copy import deepcopy


@dataclass
class MockWhoAmI:
    """Response for /whoami endpoint."""
    is_public: bool = False
    user: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    today: str = "2025-07-15"
    wiki_hash: str = "733815c19ae7c1d13f345a2b2a9aa13c67a74769"


@dataclass
class MockEmployee:
    """Employee record."""
    id: str
    name: str
    email: str
    salary: int
    location: str
    department: str
    manager: Optional[str] = None
    direct_reports: List[str] = field(default_factory=list)
    notes: str = ""
    skills: List[Dict[str, Any]] = field(default_factory=list)
    wills: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "salary": self.salary,
            "location": self.location,
            "department": self.department,
            "manager": self.manager,
            "direct_reports": self.direct_reports,
            "notes": self.notes,
            "skills": self.skills,
            "wills": self.wills,
        }


@dataclass
class MockTeamMember:
    """Project team member."""
    employee: str
    role: str  # Lead, Engineer, Designer, QA, Ops, Other
    time_slice: float = 0.0


@dataclass
class MockProject:
    """Project record."""
    id: str
    name: str
    customer: str
    status: str  # idea, exploring, active, paused, archived
    team: List[MockTeamMember] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "customer": self.customer,
            "status": self.status,
            "team": [{"employee": m.employee, "role": m.role, "time_slice": m.time_slice} for m in self.team],
            "description": self.description,
        }


@dataclass
class MockCustomer:
    """Customer record."""
    id: str
    name: str
    deal_phase: str  # idea, exploring, active, paused, archived
    location: str
    account_manager: str
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "deal_phase": self.deal_phase,
            "location": self.location,
            "account_manager": self.account_manager,
            "notes": self.notes,
        }


@dataclass
class MockTimeEntry:
    """Time entry record."""
    id: str
    employee: str
    project: Optional[str]
    customer: Optional[str]
    date: str
    hours: float
    work_category: str
    notes: str = ""
    billable: bool = True
    status: str = "draft"  # draft, submitted, approved, invoiced, voided
    logged_by: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee": self.employee,
            "project": self.project,
            "customer": self.customer,
            "date": self.date,
            "hours": self.hours,
            "work_category": self.work_category,
            "notes": self.notes,
            "billable": self.billable,
            "status": self.status,
            "logged_by": self.logged_by,
        }


# =============================================================================
# BASE DATA - mirrors the benchmark company
# =============================================================================

BASE_EMPLOYEES = [
    MockEmployee(
        id="elena_vogel",
        name="Elena Vogel",
        email="elena_vogel@aetherion.com",
        salary=180000,
        location="Munich",
        department="Executive",
        manager=None,
        direct_reports=["richard_klein", "lukas_brenner"],
        notes="CEO of Aetherion Solutions",
        skills=[{"name": "project_management", "level": 5}],
        wills=[],
    ),
    MockEmployee(
        id="richard_klein",
        name="Richard Klein",
        email="richard_klein@aetherion.com",
        salary=150000,
        location="Munich",
        department="Operations",
        manager="elena_vogel",
        direct_reports=["felix_baum", "jonas_weiss"],
        notes="COO",
        skills=[{"name": "project_management", "level": 4}],
        wills=[],
    ),
    MockEmployee(
        id="lukas_brenner",
        name="Lukas Brenner",
        email="lukas_brenner@aetherion.com",
        salary=145000,
        location="Vienna",
        department="Technology",
        manager="elena_vogel",
        direct_reports=["ana_kovac", "marko_petrovic"],
        notes="CTO",
        skills=[{"name": "cv_engineering", "level": 5}],
        wills=[],
    ),
    MockEmployee(
        id="felix_baum",
        name="Felix Baum",
        email="felix_baum@aetherion.com",
        salary=95000,
        location="Vienna",
        department="Software Engineering",
        manager="richard_klein",
        direct_reports=[],
        notes="Senior Software Engineer, CV specialist",
        skills=[{"name": "cv_engineering", "level": 4}, {"name": "machine_learning", "level": 3}],
        wills=[],
    ),
    MockEmployee(
        id="jonas_weiss",
        name="Jonas Weiss",
        email="jonas_weiss@aetherion.com",
        salary=90000,
        location="Munich",
        department="Software Engineering",
        manager="richard_klein",
        direct_reports=[],
        notes="Software Engineer",
        skills=[{"name": "backend_development", "level": 4}],
        wills=[],
    ),
    MockEmployee(
        id="helene_stutz",
        name="Helene Stutz",
        email="helene_stutz@aetherion.com",
        salary=85000,
        location="Amsterdam",
        department="Consulting",
        manager="richard_klein",
        direct_reports=[],
        notes="Consultant",
        skills=[{"name": "project_management", "level": 3}],
        wills=[],
    ),
    MockEmployee(
        id="klara_houtman",
        name="Klara Houtman",
        email="klara_houtman@aetherion.com",
        salary=115000,
        location="Amsterdam",
        department="Software Engineering",
        manager="richard_klein",
        direct_reports=[],
        notes="Lead Software Engineer",
        skills=[{"name": "cv_engineering", "level": 4}],
        wills=[],
    ),
    MockEmployee(
        id="ana_kovac",
        name="Ana Kovac",
        email="ana_kovac@aetherion.com",
        salary=88000,
        location="Vienna",
        department="Software Engineering",
        manager="lukas_brenner",
        direct_reports=[],
        notes="Software Engineer",
        skills=[{"name": "machine_learning", "level": 4}],
        wills=[],
    ),
    MockEmployee(
        id="marko_petrovic",
        name="Marko Petrovic",
        email="marko_petrovic@aetherion.com",
        salary=82000,
        location="Vienna",
        department="Software Engineering",
        manager="lukas_brenner",
        direct_reports=[],
        notes="Junior Software Engineer",
        skills=[{"name": "backend_development", "level": 2}],
        wills=[],
    ),
    MockEmployee(
        id="sofia_rinaldi",
        name="Sofia Rinaldi",
        email="sofia_rinaldi@aetherion.com",
        salary=78000,
        location="Munich",
        department="Consulting",
        manager="richard_klein",
        direct_reports=[],
        notes="Junior Consultant",
        skills=[{"name": "project_management", "level": 2}],
        wills=[],
    ),
    MockEmployee(
        id="timo_van_dijk",
        name="Timo van Dijk",
        email="timo_van_dijk@aetherion.com",
        salary=92000,
        location="Amsterdam",
        department="Software Engineering",
        manager="klara_houtman",
        direct_reports=[],
        notes="Software Engineer",
        skills=[{"name": "frontend_development", "level": 4}],
        wills=[],
    ),
    MockEmployee(
        id="mira_schaefer",
        name="Mira Schaefer",
        email="mira_schaefer@aetherion.com",
        salary=86000,
        location="Munich",
        department="Software Engineering",
        manager="jonas_weiss",
        direct_reports=[],
        notes="Software Engineer",
        skills=[{"name": "devops_engineering", "level": 3}],
        wills=[],
    ),
]

BASE_PROJECTS = [
    MockProject(
        id="proj_acme_line3_cv_poc",
        name="Line 3 Defect Detection PoC",
        customer="cust_acme_industrial_systems",
        status="active",
        team=[
            MockTeamMember(employee="jonas_weiss", role="Lead", time_slice=0.5),
            MockTeamMember(employee="felix_baum", role="Engineer", time_slice=0.3),
        ],
        description="CV-based defect detection for manufacturing line 3",
    ),
    MockProject(
        id="proj_scandifoods_packaging_cv_poc",
        name="Packaging Line CV PoC",
        customer="cust_scandi_foods_ab",
        status="exploring",
        team=[
            MockTeamMember(employee="helene_stutz", role="Lead", time_slice=0.4),
            MockTeamMember(employee="ana_kovac", role="Engineer", time_slice=0.3),
        ],
        description="Computer vision for packaging quality control",
    ),
    MockProject(
        id="proj_nordiclogistics_route_scenario_lab",
        name="Routing Scenario Lab",
        customer="cust_nordic_logistics_group",
        status="exploring",
        team=[
            MockTeamMember(employee="klara_houtman", role="Lead", time_slice=0.3),
        ],
        description="Route optimization research",
    ),
    MockProject(
        id="proj_hospital_cv_pilot",
        name="Hospital CV Pilot",
        customer="cust_central_hospital_vienna",
        status="archived",
        team=[
            MockTeamMember(employee="felix_baum", role="Lead", time_slice=0.0),
            MockTeamMember(employee="ana_kovac", role="Engineer", time_slice=0.0),
        ],
        description="Archived pilot project for hospital imaging",
    ),
    MockProject(
        id="proj_munich_edge_ai",
        name="Munich Edge AI Platform",
        customer="cust_munich_tech_hub",
        status="active",
        team=[
            MockTeamMember(employee="lukas_brenner", role="Lead", time_slice=0.2),
            MockTeamMember(employee="marko_petrovic", role="Engineer", time_slice=0.5),
        ],
        description="Edge AI deployment platform",
    ),
]

BASE_CUSTOMERS = [
    MockCustomer(
        id="cust_acme_industrial_systems",
        name="ACME Industrial Systems",
        deal_phase="active",
        location="Munich",
        account_manager="richard_klein",
        notes="Key manufacturing client",
    ),
    MockCustomer(
        id="cust_scandi_foods_ab",
        name="Scandi Foods AB",
        deal_phase="exploring",
        location="Stockholm",
        account_manager="helene_stutz",
        notes="Nordic food processing company",
    ),
    MockCustomer(
        id="cust_nordic_logistics_group",
        name="Nordic Logistics Group",
        deal_phase="exploring",
        location="Copenhagen",
        account_manager="klara_houtman",
        notes="Logistics optimization prospect",
    ),
    MockCustomer(
        id="cust_central_hospital_vienna",
        name="Central Hospital Vienna",
        deal_phase="paused",
        location="Vienna",
        account_manager="lukas_brenner",
        notes="Healthcare imaging project on hold",
    ),
    MockCustomer(
        id="cust_munich_tech_hub",
        name="Munich Tech Hub",
        deal_phase="active",
        location="Munich",
        account_manager="elena_vogel",
        notes="Technology innovation center",
    ),
]


# =============================================================================
# MockDataBuilder - for customizing test scenarios
# =============================================================================

class MockDataBuilder:
    """
    Builder for creating customized mock data sets.

    Usage:
        builder = MockDataBuilder()
        builder.with_employee_salary("klara_houtman", 120000)
        builder.with_project_status("proj_hospital_cv_pilot", "active")
        data = builder.build()
    """

    def __init__(self):
        self.employees = deepcopy(BASE_EMPLOYEES)
        self.projects = deepcopy(BASE_PROJECTS)
        self.customers = deepcopy(BASE_CUSTOMERS)
        self.time_entries: List[MockTimeEntry] = []
        self.api_errors: Dict[str, Exception] = {}
        self.custom_responses: Dict[str, Any] = {}

    def with_employee_salary(self, emp_id: str, salary: int) -> 'MockDataBuilder':
        """Override employee salary."""
        for emp in self.employees:
            if emp.id == emp_id:
                emp.salary = salary
                break
        return self

    def with_employee_location(self, emp_id: str, location: str) -> 'MockDataBuilder':
        """Override employee location."""
        for emp in self.employees:
            if emp.id == emp_id:
                emp.location = location
                break
        return self

    def with_project_status(self, proj_id: str, status: str) -> 'MockDataBuilder':
        """Override project status."""
        for proj in self.projects:
            if proj.id == proj_id:
                proj.status = status
                break
        return self

    def with_project_team(self, proj_id: str, team: List[MockTeamMember]) -> 'MockDataBuilder':
        """Override project team."""
        for proj in self.projects:
            if proj.id == proj_id:
                proj.team = team
                break
        return self

    def with_time_entry(self, entry: MockTimeEntry) -> 'MockDataBuilder':
        """Add a time entry."""
        self.time_entries.append(entry)
        return self

    def with_api_error(self, endpoint: str, error: Exception) -> 'MockDataBuilder':
        """Simulate API error for specific endpoint."""
        self.api_errors[endpoint] = error
        return self

    def with_custom_response(self, endpoint: str, response: Any) -> 'MockDataBuilder':
        """Override response for specific endpoint."""
        self.custom_responses[endpoint] = response
        return self

    def add_employee(self, employee: MockEmployee) -> 'MockDataBuilder':
        """Add new employee."""
        self.employees.append(employee)
        return self

    def add_project(self, project: MockProject) -> 'MockDataBuilder':
        """Add new project."""
        self.projects.append(project)
        return self

    def build(self) -> 'MockData':
        """Build the mock data instance."""
        return MockData(
            employees=self.employees,
            projects=self.projects,
            customers=self.customers,
            time_entries=self.time_entries,
            api_errors=self.api_errors,
            custom_responses=self.custom_responses,
        )


@dataclass
class MockData:
    """Container for all mock data."""
    employees: List[MockEmployee]
    projects: List[MockProject]
    customers: List[MockCustomer]
    time_entries: List[MockTimeEntry]
    api_errors: Dict[str, Exception]
    custom_responses: Dict[str, Any]

    def get_employee(self, emp_id: str) -> Optional[MockEmployee]:
        """Get employee by ID."""
        for emp in self.employees:
            if emp.id == emp_id:
                return emp
        return None

    def get_project(self, proj_id: str) -> Optional[MockProject]:
        """Get project by ID."""
        for proj in self.projects:
            if proj.id == proj_id:
                return proj
        return None

    def get_customer(self, cust_id: str) -> Optional[MockCustomer]:
        """Get customer by ID."""
        for cust in self.customers:
            if cust.id == cust_id:
                return cust
        return None

    def search_employees(self, query: str = None, location: str = None,
                         department: str = None, manager: str = None) -> List[MockEmployee]:
        """Search employees with filters."""
        results = self.employees

        if query:
            query_lower = query.lower()
            results = [e for e in results if
                       query_lower in e.name.lower() or
                       query_lower in e.id.lower()]

        if location:
            results = [e for e in results if e.location.lower() == location.lower()]

        if department:
            results = [e for e in results if e.department.lower() == department.lower()]

        if manager:
            results = [e for e in results if e.manager == manager]

        return results

    def search_projects(self, query: str = None, customer_id: str = None,
                        status: List[str] = None, member: str = None) -> List[MockProject]:
        """Search projects with filters."""
        results = self.projects

        if query:
            query_lower = query.lower()
            results = [p for p in results if
                       query_lower in p.name.lower() or
                       query_lower in p.id.lower()]

        if customer_id:
            results = [p for p in results if p.customer == customer_id]

        if status:
            results = [p for p in results if p.status in status]

        if member:
            results = [p for p in results if any(m.employee == member for m in p.team)]

        return results

    def search_customers(self, query: str = None, locations: List[str] = None,
                         deal_phase: List[str] = None, account_managers: List[str] = None) -> List[MockCustomer]:
        """Search customers with filters."""
        results = self.customers

        if query:
            query_lower = query.lower()
            results = [c for c in results if
                       query_lower in c.name.lower() or
                       query_lower in c.id.lower()]

        if locations:
            locations_lower = [l.lower() for l in locations]
            results = [c for c in results if c.location.lower() in locations_lower]

        if deal_phase:
            results = [c for c in results if c.deal_phase in deal_phase]

        if account_managers:
            results = [c for c in results if c.account_manager in account_managers]

        return results
