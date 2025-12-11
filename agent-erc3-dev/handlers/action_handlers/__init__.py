"""
Action handlers module.

Provides specialized handlers for different action types using Strategy pattern.
"""
from .base import ActionHandler, CompositeActionHandler
from .wiki import WikiSearchHandler, WikiLoadHandler
from .project_search import ProjectSearchHandler
from .employee_search import EmployeeSearchHandler

__all__ = [
    'ActionHandler',
    'CompositeActionHandler',
    'WikiSearchHandler',
    'WikiLoadHandler',
    'ProjectSearchHandler',
    'EmployeeSearchHandler',
]
