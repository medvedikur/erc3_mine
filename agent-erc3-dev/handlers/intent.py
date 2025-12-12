"""
Intent Detection - analyzes task text to determine operation intent.

This module extracts intent detection logic from core.py to improve
maintainability and testability.
"""
import re
from typing import Optional, Set
from dataclasses import dataclass


@dataclass
class TaskIntent:
    """Detected intent from task text."""
    # Employee update intents
    is_salary_only: bool = False
    is_skill_update: bool = False
    is_location_update: bool = False

    # Time logging intents
    is_time_logging: bool = False

    # Project intents
    is_project_modification: bool = False
    is_project_status_change: bool = False

    # General
    is_destructive: bool = False
    mentioned_keywords: Set[str] = None

    def __post_init__(self):
        if self.mentioned_keywords is None:
            self.mentioned_keywords = set()


class IntentDetector:
    """
    Detects task intent from task text.

    Replaces inline regex checks with a centralized, testable component.
    """

    # Salary-related keywords
    SALARY_KEYWORDS = {'salary', 'compensation', 'pay', 'wage', 'bonus'}
    SALARY_ACTION_KEYWORDS = {'raise', 'increase', 'decrease', 'adjust', 'update', 'change'}

    # Other employee fields that indicate NOT salary-only
    NON_SALARY_KEYWORDS = {'skill', 'note', 'location', 'department', 'wills'}

    # Time logging patterns
    TIME_LOG_PATTERNS = [
        r'\blog\s+\d+\s*hours?\b',
        r'\b\d+\s*hours?\s+of\b',
        r'\bbillable\s+work\b',
        r'\blog\s+time\b',
        r'\btime\s+entry\b',
        r'\btrack\s+time\b',
    ]

    # Project modification patterns
    PROJECT_MOD_PATTERNS = [
        r'\bpause\b.{0,50}\bproject\b',
        r'\barchive\b.{0,50}\bproject\b',
        r'\bchange\s+project\s+status\b',
        r'\bupdate\s+project\s+status\b',
        r'\bset\s+project\s+to\b',
        r'\bswitch\s+project\b',
        r'\bproject\b.{0,30}\bto\s+(paused|archived|active)\b',
    ]

    # Destructive operation keywords
    DESTRUCTIVE_KEYWORDS = {
        'wipe', 'delete', 'erase', 'destroy', 'purge',
        'remove all', 'clear all'
    }

    def __init__(self):
        self._time_log_re = re.compile('|'.join(self.TIME_LOG_PATTERNS), re.IGNORECASE)
        self._project_mod_re = re.compile('|'.join(self.PROJECT_MOD_PATTERNS), re.IGNORECASE)

    def detect(self, task_text: Optional[str]) -> TaskIntent:
        """
        Detect intent from task text.

        Args:
            task_text: The task text to analyze

        Returns:
            TaskIntent with detected flags
        """
        if not task_text:
            return TaskIntent()

        text_lower = task_text.lower()

        # Detect mentioned keywords
        mentioned = set()
        for kw in self.SALARY_KEYWORDS | self.NON_SALARY_KEYWORDS | self.SALARY_ACTION_KEYWORDS:
            if kw in text_lower:
                mentioned.add(kw)

        # Detect salary-only intent
        has_salary_keyword = bool(mentioned & self.SALARY_KEYWORDS)
        has_salary_action = bool(mentioned & self.SALARY_ACTION_KEYWORDS)
        has_non_salary = bool(mentioned & self.NON_SALARY_KEYWORDS)
        is_salary_only = has_salary_keyword and has_salary_action and not has_non_salary

        # Detect time logging intent
        is_time_logging = bool(self._time_log_re.search(task_text))

        # Detect project modification intent
        is_project_modification = bool(self._project_mod_re.search(task_text))

        # Detect destructive intent
        is_destructive = any(kw in text_lower for kw in self.DESTRUCTIVE_KEYWORDS)

        return TaskIntent(
            is_salary_only=is_salary_only,
            is_skill_update='skill' in text_lower,
            is_location_update='location' in text_lower,
            is_time_logging=is_time_logging,
            is_project_modification=is_project_modification,
            is_destructive=is_destructive,
            mentioned_keywords=mentioned,
        )


# Singleton instance for convenience
_detector = IntentDetector()


def detect_intent(task_text: Optional[str]) -> TaskIntent:
    """
    Convenience function to detect intent from task text.

    Args:
        task_text: The task text to analyze

    Returns:
        TaskIntent with detected flags
    """
    return _detector.detect(task_text)
