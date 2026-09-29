"""RFC 6901 JSON Pointers: how every citation addresses its evidence in a raw study record."""

import json
from functools import lru_cache
from typing import Any

PHASES = "/protocolSection/designModule/phases"
STUDY_TYPE = "/protocolSection/designModule/studyType"
ENROLLMENT_COUNT = "/protocolSection/designModule/enrollmentInfo/count"
ENROLLMENT_TYPE = "/protocolSection/designModule/enrollmentInfo/type"
NCT_ID = "/protocolSection/identificationModule/nctId"
OVERALL_STATUS = "/protocolSection/statusModule/overallStatus"
START_DATE = "/protocolSection/statusModule/startDateStruct/date"
START_DATE_TYPE = "/protocolSection/statusModule/startDateStruct/type"
COMPLETION_DATE = "/protocolSection/statusModule/completionDateStruct/date"
COMPLETION_DATE_TYPE = "/protocolSection/statusModule/completionDateStruct/type"
LEAD_SPONSOR_NAME = "/protocolSection/sponsorCollaboratorsModule/leadSponsor/name"
LEAD_SPONSOR_CLASS = "/protocolSection/sponsorCollaboratorsModule/leadSponsor/class"
COLLABORATORS = "/protocolSection/sponsorCollaboratorsModule/collaborators"
INTERVENTIONS = "/protocolSection/armsInterventionsModule/interventions"
ARM_GROUPS = "/protocolSection/armsInterventionsModule/armGroups"
CONDITIONS = "/protocolSection/conditionsModule/conditions"
KEYWORDS = "/protocolSection/conditionsModule/keywords"
LOCATIONS = "/protocolSection/contactsLocationsModule/locations"
INTERVENTION_MESH = "/derivedSection/interventionBrowseModule/meshes"
CONDITION_MESH = "/derivedSection/conditionBrowseModule/meshes"
CONDITION_ANCESTORS = "/derivedSection/conditionBrowseModule/ancestors"


def _escape(token: str | int) -> str:
    """Escape one raw token per RFC 6901 (~ before /, so order matters)."""
    return str(token).replace("~", "~0").replace("/", "~1")


def build_pointer(*tokens: str | int) -> str:
    """Join raw tokens into a pointer, escaping '~' and '/' per RFC 6901."""
    return "".join(f"/{_escape(t)}" for t in tokens)


POINTER_CACHE_SIZE = 16384


@lru_cache(maxsize=POINTER_CACHE_SIZE)
def _tokens(pointer: str) -> tuple[str, ...]:
    """Split and unescape a pointer once (the verifier resolves the same few paths millions of
    times); '~1' before '~0' so an escaped '~01' decodes to '~1', per RFC 6901."""
    if not pointer.startswith("/"):
        raise KeyError(f"JSON Pointer must start with '/': {pointer!r}")
    return tuple(raw.replace("~1", "/").replace("~0", "~") for raw in pointer[1:].split("/"))


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Return the single value at `pointer`; raise KeyError/IndexError if it does not exist."""
    if pointer == "":
        return document
    node = document
    for token in _tokens(pointer):
        if isinstance(node, list):
            if not token.isdigit():
                raise KeyError(f"expected an array index, got {token!r}")
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node[token]
        else:
            raise KeyError(f"cannot descend into {type(node).__name__}")
    return node


def json_text(value: Any) -> str:
    """Render a scalar exactly as it appears in the API's JSON (so excerpts are exact text)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return json.dumps(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"excerpts must be scalars, got {type(value).__name__}")
