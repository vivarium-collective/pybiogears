"""pybiogears: a Python binding for the BioGears physiology engine."""

from ._pybiogears import BioGearsThunk, DataRequest, EventChange, LogMessage
from .engine import BioGearsEngine, BioGearsError, IrreversibleStateError
from .pool import EnginePool
from .schema import Raw, SchemaError, action, condition, configuration
from . import actions, schema

__all__ = [
    "BioGearsEngine",
    "BioGearsError",
    "BioGearsThunk",
    "DataRequest",
    "EnginePool",
    "Raw",
    "SchemaError",
    "action",
    "condition",
    "configuration",
    "schema",
    "EventChange",
    "IrreversibleStateError",
    "LogMessage",
    "actions",
    "biogears_version",
]


def biogears_version() -> str:
    return BioGearsThunk.version()
