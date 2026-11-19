"""pybiogears: a Python binding for the BioGears physiology engine."""

from ._pybiogears import BioGearsThunk, DataRequest, EventChange, LogMessage
from .engine import BioGearsEngine, BioGearsError, IrreversibleStateError
from .pool import EnginePool
from .schema import Raw, SchemaError, action, condition, configuration
from .data import DataGenerationError, GeneratedData, generate_data
from . import actions, data, schema

__all__ = [
    "BioGearsEngine",
    "BioGearsError",
    "BioGearsThunk",
    "DataGenerationError",
    "DataRequest",
    "EnginePool",
    "GeneratedData",
    "Raw",
    "SchemaError",
    "action",
    "condition",
    "configuration",
    "data",
    "generate_data",
    "schema",
    "EventChange",
    "IrreversibleStateError",
    "LogMessage",
    "actions",
    "biogears_version",
]


def biogears_version() -> str:
    return BioGearsThunk.version()
