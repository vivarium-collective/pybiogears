"""BioGears actions and conditions as Python calls, read from BioGears' own XML schema.

Pulse's Python API has a class per action (``SEHemorrhage``, ...). BioGears' data model
is its XML schema (``<runtime>/xsd``), and its scenario parser is the public way to
create actions (``BioGearsThunk::process_actions``), so pybiogears builds the action
XML from the schema instead: every action and condition type BioGears 8.2.0 defines is
available, with its fields checked before BioGears sees them.

    >>> from pybiogears import schema
    >>> schema.action("Hemorrhage", Compartment="RightLeg", InitialRate=(150, "mL/min"))
    '<Action xsi:type="HemorrhageData" Compartment="RightLeg"><InitialRate value="150.0" unit="mL/min"/></Action>'

Values, by the field's schema type:

- a scalar (``Scalar...Data``): a number, or ``(value, unit)``; the unit is required
  exactly when the schema requires it (``ScalarVolumePerTimeData``: yes;
  ``ScalarFractionData``: no). BioGears checks the unit itself;
- an enumeration: one of its values (``"On"``, ``"Severe"``);
- text and numbers (``xs:string``, ``xs:double``, ``xs:boolean``, ...): a str, number or bool;
- a nested structure (``NutritionData``): a dict of its fields, the same way; a dict may
  name a derived type with the key ``"xsi:type"``;
- a repeated field (``maxOccurs > 1``): a list;
- :class:`Raw`: XML inserted as given (an escape hatch).

Field names are the schema's (``InitialRate``); snake_case (``initial_rate``) also works.
A ``choice`` group (ConsumeNutrients: ``Nutrition`` or ``NutritionFile``) takes exactly
one of its fields when required, at most one otherwise.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

XS = "{http://www.w3.org/2001/XMLSchema}"
RUNTIME_ENV = "BIOGEARS_RUNTIME"
_SIMPLE = {"string", "double", "float", "int", "integer", "long", "boolean", "unsignedInt", "decimal",
           "nonNegativeInteger", "positiveInteger", "short", "dateTime", "anyURI"}


class SchemaError(ValueError):
    """A value that BioGears' schema doesn't allow."""


@dataclass(frozen=True)
class Raw:
    """XML inserted as given."""
    xml: str


@dataclass(frozen=True)
class Field:
    """One field of a schema type."""
    name: str
    type: str                 # schema type name, e.g. ScalarVolumePerTimeData, xs:string, enumOnOff
    kind: str                 # "scalar", "enum", "simple", "complex"
    attribute: bool           # an XML attribute (else an element)
    required: bool
    repeated: bool = False
    unit_required: bool = False
    choices: tuple[str, ...] = ()
    group: int | None = None  # choice group id; fields sharing it are alternatives
    group_required: bool = False

    def __str__(self):
        bits = [self.type]
        if self.kind == "enum":
            bits = [" | ".join(self.choices)]
        elif self.kind == "scalar":
            bits.append("(value, unit)" if self.unit_required else "number")
        if self.repeated:
            bits.append("list")
        if self.group is not None:
            bits.append(f"choice {self.group}")
        return f"{self.name}{'' if self.required else '?'}: {', '.join(bits)}"


@dataclass
class _Type:
    name: str
    base: str | None = None
    derivation: str | None = None     # extension, restriction
    particles: list = field(default_factory=list)   # [(element-node, group, group_required)]
    attributes: list = field(default_factory=list)  # attribute nodes
    simple: bool = False
    enum: tuple[str, ...] = ()


def _local(name: str | None) -> str | None:
    return None if name is None else name.split(":")[-1]


class Schema:
    """BioGears' XML schema: every complex and simple type of ``<runtime>/xsd``."""

    def __init__(self, xsd_dir: Path):
        self.types: dict[str, _Type] = {}
        self._group = 0
        for path in sorted(Path(xsd_dir).rglob("*.xsd")):
            for node in ET.parse(path).getroot():
                if node.tag == XS + "complexType" and node.get("name"):
                    self.types[node.get("name")] = self._complex(node)
                elif node.tag == XS + "simpleType" and node.get("name"):
                    self.types[node.get("name")] = self._simple(node)

    # -- parsing -----------------------------------------------------------------
    def _simple(self, node) -> _Type:
        t = _Type(node.get("name"), simple=True)
        r = node.find(XS + "restriction")
        if r is not None:
            t.base = _local(r.get("base"))
            t.enum = tuple(e.get("value") for e in r.findall(XS + "enumeration"))
        return t

    def _complex(self, node) -> _Type:
        t = _Type(node.get("name"))
        body = node
        for content in (XS + "complexContent", XS + "simpleContent"):
            c = node.find(content)
            if c is not None:
                for d in ("extension", "restriction"):
                    der = c.find(XS + d)
                    if der is not None:
                        t.base, t.derivation, body = _local(der.get("base")), d, der
        self._particles(body, t, None, False)
        t.attributes = body.findall(XS + "attribute")
        return t

    def _particles(self, node, t: _Type, group, group_required):
        for child in node:
            if child.tag == XS + "element":
                t.particles.append((child, group, group_required))
            elif child.tag == XS + "sequence":
                self._particles(child, t, group, group_required)
            elif child.tag == XS + "choice":
                self._group += 1
                self._particles(child, t, self._group, child.get("minOccurs", "1") != "0")

    # -- queries -----------------------------------------------------------------
    def chain(self, name: str) -> list[_Type]:
        """``name`` and its base types, most derived first."""
        out = []
        while name in self.types:
            t = self.types[name]
            out.append(t)
            name = t.base
        return out

    def derives(self, name: str, base: str) -> bool:
        return any(t.name == base for t in self.chain(name))

    def is_scalar(self, name: str) -> bool:
        return self.derives(name, "ScalarData")

    def unit_required(self, name: str) -> bool:
        for t in self.chain(name):
            for a in t.attributes:
                if a.get("name") == "unit":
                    return a.get("use") == "required"
        return False

    def enum(self, name: str) -> tuple[str, ...]:
        for t in self.chain(name):
            if t.enum:
                return t.enum
        return ()

    def fields(self, name: str) -> list[Field]:
        """The fields of complex type ``name``, base type's first, in schema order."""
        if name not in self.types or self.types[name].simple:
            raise SchemaError(f"{name} is not a complex type of BioGears' schema")
        out: list[Field] = []
        for t in reversed(self.chain(name)):
            if t.derivation == "restriction":
                continue  # a restriction narrows the base's attributes (scalars only here)
            for a in t.attributes:
                out.append(self._field(a, attribute=True))
            for el, group, group_required in t.particles:
                out.append(self._field(el, attribute=False, group=group, group_required=group_required))
        return out

    def _field(self, node, *, attribute: bool, group=None, group_required=False) -> Field:
        name = node.get("name") or _local(node.get("ref"))
        tname = _local(node.get("type")) or "string"
        if attribute:
            required = node.get("use") == "required"
            repeated = False
        else:
            required = node.get("minOccurs", "1") != "0" and group is None
            repeated = node.get("maxOccurs", "1") not in ("0", "1")
        if tname in _SIMPLE and tname not in self.types:
            kind, typ = "simple", "xs:" + tname
        elif self.enum(tname):
            kind, typ = "enum", tname
        elif tname in self.types and self.types[tname].simple:
            kind, typ = "simple", tname
        elif self.is_scalar(tname):
            kind, typ = "scalar", tname
        else:
            kind, typ = "complex", tname
        return Field(name, typ, kind, attribute, required, repeated,
                     self.unit_required(tname) if kind == "scalar" else False,
                     self.enum(tname) if kind == "enum" else (), group, group_required)

    def concrete_subtypes(self, base: str) -> list[str]:
        return sorted(n for n, t in self.types.items() if not t.simple and n != base and self.derives(n, base))

    # -- building ----------------------------------------------------------------
    def build(self, tag: str, type_name: str, values: dict[str, Any], *, xsi_type: bool) -> str:
        fields = self.fields(type_name)
        by_key = {}
        for f in fields:
            by_key[f.name] = f
            by_key[_norm(f.name)] = f
        given: dict[str, Any] = {}
        for key, value in values.items():
            if key == "xsi:type":
                continue
            f = by_key.get(key) or by_key.get(_norm(key))
            if f is None:
                raise SchemaError(f"{_short(type_name)} has no field {key!r}; fields: {[x.name for x in fields]}")
            if f.name in given:
                raise SchemaError(f"{_short(type_name)}: {f.name} given twice")
            if value is not None:
                given[f.name] = value
        for f in fields:
            if f.required and f.name not in given:
                raise SchemaError(f"{_short(type_name)}: {f.name} is required ({f})")
        groups: dict[int, list[Field]] = {}
        for f in fields:
            if f.group is not None:
                groups.setdefault(f.group, []).append(f)
        for members in groups.values():
            chosen = [f.name for f in members if f.name in given]
            if len(chosen) > 1:
                raise SchemaError(f"{_short(type_name)}: give one of {[f.name for f in members]}, not {chosen}")
            if not chosen and members[0].group_required:
                raise SchemaError(f"{_short(type_name)}: one of {[f.name for f in members]} is required")
        attrs = [f' xsi:type="{type_name}"'] if xsi_type else []
        body = []
        for f in fields:
            if f.name not in given:
                continue
            value = given[f.name]
            if f.attribute:
                attrs.append(f" {f.name}={quoteattr(self._text(f, value))}")
                continue
            items = value if f.repeated and isinstance(value, (list, tuple)) and not _is_scalar_pair(f, value) else [value]
            if not f.repeated and len(items) != 1:
                raise SchemaError(f"{_short(type_name)}: {f.name} takes one value")
            for item in items:
                body.append(self._element(f, item))
        inner = "".join(body)
        return f"<{tag}{''.join(attrs)}>{inner}</{tag}>" if inner else f"<{tag}{''.join(attrs)}/>"

    def _element(self, f: Field, value) -> str:
        if isinstance(value, Raw):
            return value.xml
        if f.kind == "scalar":
            return self._scalar(f, value)
        if f.kind == "complex":
            if not isinstance(value, dict):
                raise SchemaError(f"{f.name} ({f.type}) takes a dict of its fields, got {value!r}")
            sub = value.get("xsi:type")
            if sub is not None:
                sub = sub if sub in self.types else sub + "Data"
                if not self.derives(sub, f.type):
                    raise SchemaError(f"{f.name}: {sub} is not a {f.type}")
            return self.build(f.name, sub or f.type, value, xsi_type=sub is not None)
        return f"<{f.name}>{escape(self._text(f, value))}</{f.name}>"

    def _scalar(self, f: Field, value) -> str:
        if isinstance(value, (tuple, list)) and len(value) == 2 and isinstance(value[1], str):
            number, unit = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            number, unit = value, ""
        else:
            raise SchemaError(f"{f.name} ({f.type}) takes a number or (value, unit), got {value!r}")
        if f.unit_required and not unit:
            raise SchemaError(f"{f.name} ({f.type}) needs a unit: (value, unit)")
        number = float(number)
        u = f" unit={quoteattr(unit)}" if unit else ""
        return f'<{f.name} value="{number!r}"{u}/>'

    def _text(self, f: Field, value) -> str:
        if f.kind == "enum":
            if value not in f.choices:
                raise SchemaError(f"{f.name} must be one of {list(f.choices)}, got {value!r}")
            return value
        if f.kind == "scalar" or f.kind == "complex":
            raise SchemaError(f"{f.name} ({f.type}) can't be an attribute value")
        if isinstance(value, bool):
            if f.type != "xs:boolean":
                raise SchemaError(f"{f.name} ({f.type}) got a bool")
            return "true" if value else "false"
        if f.type == "xs:boolean":
            raise SchemaError(f"{f.name} takes True or False, got {value!r}")
        if f.type in ("xs:double", "xs:float", "xs:decimal"):
            if not isinstance(value, (int, float)):
                raise SchemaError(f"{f.name} ({f.type}) takes a number, got {value!r}")
            return repr(float(value))
        if f.type in ("xs:int", "xs:integer", "xs:long", "xs:short", "xs:unsignedInt",
                      "xs:nonNegativeInteger", "xs:positiveInteger"):
            if not isinstance(value, int):
                raise SchemaError(f"{f.name} ({f.type}) takes an integer, got {value!r}")
            return str(value)
        return str(value)


def _is_scalar_pair(f: Field, value) -> bool:
    return f.kind == "scalar" and len(value) == 2 and isinstance(value[1], str)


def _norm(name: str) -> str:
    return name.replace("_", "").lower()


def _short(type_name: str) -> str:
    return type_name[:-4] if type_name.endswith("Data") else type_name


def _xsd_dir(runtime: str | os.PathLike | None) -> Path:
    runtime = runtime or os.environ.get(RUNTIME_ENV)
    if not runtime:
        raise SchemaError(f"no BioGears runtime directory: pass runtime or set ${RUNTIME_ENV}")
    xsd = Path(runtime).expanduser() / "xsd"
    if not xsd.is_dir():
        raise SchemaError(f"no xsd/ directory in the BioGears runtime {runtime}")
    return xsd.resolve()


@lru_cache(maxsize=4)
def _load(xsd_dir: Path) -> Schema:
    return Schema(xsd_dir)


def schema(runtime: str | os.PathLike | None = None) -> Schema:
    """BioGears' schema from ``<runtime>/xsd`` (default ``$BIOGEARS_RUNTIME``), cached."""
    return _load(_xsd_dir(runtime))


# Base types that are categories, not actions/conditions one can send.
_ABSTRACT = {"ActionData", "PatientActionData", "EnvironmentActionData", "AnesthesiaMachineActionData",
             "InhalerActionData", "SubstanceAdministrationData", "ConditionData", "PatientConditionData",
             "EnvironmentConditionData", "AdvanceTimeData", "SerializeStateData", "ExampleActionData"}


def _resolve(s: Schema, name: str, base: str) -> str:
    candidates = [name, name + "Data"]
    for c in candidates:
        if c in s.types and s.derives(c, base) and c not in _ABSTRACT:
            return c
    pool = _names(s, base)
    close = [n for n in pool if _norm(n) == _norm(name.removesuffix("Data"))]
    if close:
        return close[0] + "Data"
    raise SchemaError(f"{name!r} is not a BioGears {base[:-4].lower()}; choose from {pool}")


def _names(s: Schema, base: str) -> list[str]:
    return [_short(n) for n in s.concrete_subtypes(base) if n not in _ABSTRACT]


def actions(runtime=None) -> list[str]:
    """Every action type BioGears defines (``Hemorrhage``, ``Infection``, ...)."""
    return _names(schema(runtime), "ActionData")


def conditions(runtime=None) -> list[str]:
    """Every condition type BioGears defines (``ChronicAnemia``, ``Dehydration``, ...)."""
    return _names(schema(runtime), "ConditionData")


def describe(name: str, runtime=None) -> list[Field]:
    """The fields of an action, condition or other schema type (``Hemorrhage``, ``NutritionData``)."""
    s = schema(runtime)
    for base in ("ActionData", "ConditionData"):
        try:
            return s.fields(_resolve(s, name, base))
        except SchemaError:
            pass
    return s.fields(name if name in s.types else name + "Data")


def action(name: str, runtime=None, **fields) -> str:
    """BioGears ``<Action>`` XML for action ``name`` (see the module docstring for values),
    for :meth:`pybiogears.BioGearsEngine.process_actions`."""
    s = schema(runtime)
    return s.build("Action", _resolve(s, name, "ActionData"), fields, xsi_type=True)


def condition(name: str, runtime=None, **fields) -> str:
    """BioGears ``<Condition>`` XML for condition ``name``, for
    :meth:`pybiogears.BioGearsEngine.initialize`."""
    s = schema(runtime)
    return s.build("Condition", _resolve(s, name, "ConditionData"), fields, xsi_type=True)


def configuration(runtime=None, **fields) -> str:
    """Children of a ``<BioGearsConfiguration>`` (``TimeStep``, ``WritePatientBaselineFile``,
    ``RenalConfiguration``, ...), for the ``configuration`` argument of
    :meth:`pybiogears.BioGearsEngine.initialize` and :meth:`~pybiogears.BioGearsEngine.load_state`."""
    xml = schema(runtime).build("C", "BioGearsConfigurationData", fields, xsi_type=False)
    return "" if xml == "<C/>" else xml[len("<C>"):-len("</C>")]

