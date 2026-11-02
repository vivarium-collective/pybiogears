"""pybiogears: a Python binding for the BioGears physiology engine."""

from ._pybiogears import BioGearsThunk, DataRequest, EventChange, LogMessage
from .engine import BioGearsEngine, BioGearsError
from . import actions

__all__ = [
    "BioGearsEngine",
    "BioGearsError",
    "BioGearsThunk",
    "DataRequest",
    "EventChange",
    "LogMessage",
    "actions",
    "biogears_version",
]


def biogears_version() -> str:
    return BioGearsThunk.version()
