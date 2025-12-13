"""
Test scenario definition and task builder.

Defines the structure for test cases and provides builders for creating them.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable

from .mock_data import MockWhoAmI, MockDataBuilder, MockData


@dataclass
class AgentLink:
    """Link reference in agent response."""
    kind: str  # employee, customer, project, wiki, location
    id: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id}

    @classmethod
    def employee(cls, emp_id: str) -> 'AgentLink':
        return cls(kind="employee", id=emp_id)

    @classmethod
    def project(cls, proj_id: str) -> 'AgentLink':
        return cls(kind="project", id=proj_id)

    @classmethod
    def customer(cls, cust_id: str) -> 'AgentLink':
        return cls(kind="customer", id=cust_id)


@dataclass
class ExpectedResult:
    """Expected result of a test."""
    outcome: str  # ok_answer, ok_not_found, denied_security, none_clarification_needed, none_unsupported, error_internal
    links: List[AgentLink] = field(default_factory=list)
    message_contains: Optional[List[str]] = None  # Optional: message must contain these strings
    message_not_contains: Optional[List[str]] = None  # Optional: message must NOT contain these

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "links": [l.to_dict() for l in self.links],
        }


@dataclass
class TestScenario:
    """
    Complete test scenario definition.

    A test scenario includes:
    - Task metadata (spec_id, description)
    - User context (identity, date, wiki version)
    - Mock data configuration
    - Expected results
    """

    # Identification
    spec_id: str
    description: str
    category: str  # Identity, Authorization, Search, ErrorHandling, Security, TimeTracking, Wiki

    # Task input
    task_text: str

    # User context
    identity: MockWhoAmI

    # Expected result
    expected: ExpectedResult

    # Mock data customization (optional)
    data_builder: Optional[MockDataBuilder] = None

    # Related tests (for documentation)
    related_tests: List[str] = field(default_factory=list)

    # Potential error this test catches
    potential_error: str = ""

    # API methods expected to be called
    expected_api_calls: List[str] = field(default_factory=list)

    # Custom validator function (optional)
    custom_validator: Optional[Callable[[Any, List[Any]], bool]] = None

    def get_mock_data(self) -> MockData:
        """Get mock data for this scenario."""
        if self.data_builder:
            return self.data_builder.build()
        return MockDataBuilder().build()


def create_guest_scenario(
    spec_id: str,
    task_text: str,
    description: str,
    expected_outcome: str,
    expected_links: List[AgentLink] = None,
    today: str = "2025-07-15",
    category: str = "Identity",
    **kwargs
) -> TestScenario:
    """Helper to create a guest (public) user scenario."""
    return TestScenario(
        spec_id=spec_id,
        description=description,
        category=category,
        task_text=task_text,
        identity=MockWhoAmI(
            is_public=True,
            user=None,
            today=today,
        ),
        expected=ExpectedResult(
            outcome=expected_outcome,
            links=expected_links or [],
        ),
        **kwargs
    )


def create_employee_scenario(
    spec_id: str,
    task_text: str,
    description: str,
    user_id: str,
    expected_outcome: str,
    expected_links: List[AgentLink] = None,
    today: str = "2025-07-15",
    category: str = "Authorization",
    data_builder: MockDataBuilder = None,
    **kwargs
) -> TestScenario:
    """Helper to create an authenticated employee scenario."""
    # Find employee info from base data
    from .mock_data import BASE_EMPLOYEES
    emp = next((e for e in BASE_EMPLOYEES if e.id == user_id), None)

    identity = MockWhoAmI(
        is_public=False,
        user=user_id,
        name=emp.name if emp else user_id,
        email=emp.email if emp else f"{user_id}@aetherion.com",
        department=emp.department if emp else "Unknown",
        location=emp.location if emp else "Unknown",
        today=today,
    )

    return TestScenario(
        spec_id=spec_id,
        description=description,
        category=category,
        task_text=task_text,
        identity=identity,
        expected=ExpectedResult(
            outcome=expected_outcome,
            links=expected_links or [],
        ),
        data_builder=data_builder,
        **kwargs
    )


# =============================================================================
# Standard identity presets
# =============================================================================

def identity_guest(today: str = "2025-07-15") -> MockWhoAmI:
    """Public/guest user identity."""
    return MockWhoAmI(is_public=True, user=None, today=today)


def identity_ceo(today: str = "2025-07-15") -> MockWhoAmI:
    """CEO (elena_vogel) identity - Level 1."""
    return MockWhoAmI(
        is_public=False,
        user="elena_vogel",
        name="Elena Vogel",
        email="elena_vogel@aetherion.com",
        department="Executive",
        location="Munich",
        today=today,
    )


def identity_coo(today: str = "2025-07-15") -> MockWhoAmI:
    """COO (sofia_rinaldi) identity - Level 1 Executive."""
    return MockWhoAmI(
        is_public=False,
        user="sofia_rinaldi",
        name="Sofia Rinaldi",
        email="sofia_rinaldi@aetherion.com",
        department="Executive Leadership",  # COO = Level 1 Executive
        location="Munich",
        today=today,
    )


def identity_consultant(today: str = "2025-07-15") -> MockWhoAmI:
    """Consultant (helene_stutz) identity - Level 3."""
    return MockWhoAmI(
        is_public=False,
        user="helene_stutz",
        name="Helene Stutz",
        email="helene_stutz@aetherion.com",
        department="Consulting",
        location="Amsterdam",
        today=today,
    )


def identity_engineer(user_id: str = "felix_baum", today: str = "2025-07-15") -> MockWhoAmI:
    """Engineer identity - Level 3."""
    from .mock_data import BASE_EMPLOYEES
    emp = next((e for e in BASE_EMPLOYEES if e.id == user_id), None)
    return MockWhoAmI(
        is_public=False,
        user=user_id,
        name=emp.name if emp else user_id,
        email=emp.email if emp else f"{user_id}@aetherion.com",
        department=emp.department if emp else "Software Engineering",
        location=emp.location if emp else "Vienna",
        today=today,
    )
