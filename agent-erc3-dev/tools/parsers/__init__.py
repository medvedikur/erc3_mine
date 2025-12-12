"""
Tool Parsers - organized by domain.

Parsers are registered via @ToolParser.register decorator.
This module imports all parsers to register them at module load time.
"""

# Import all parser modules to trigger registration
from . import identity
from . import employees
from . import wiki
from . import customers
from . import projects
from . import time
from . import response
