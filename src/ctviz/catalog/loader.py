"""Load and validate `catalog.yaml`: the single source of truth the planner, schema and judge
share (PLAN.md §5.10). Adding a dimension means adding a YAML entry, with no prompt surgery.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from ctviz.schemas.enums import Dimension, Measure, NetworkType, SearchParam, TimeField

CATALOG_PATH = Path(__file__).parent / "catalog.yaml"


class _CatalogEntry(BaseModel):
    """Shared config for every catalog leaf: frozen, and no keys beyond what's declared."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SearchParamEntry(_CatalogEntry):
    """One `query.*` param: what it provides, and when to use (or avoid) it."""

    provides: str
    use_for: list[str] = []
    avoid_for: list[str] = []


class MenuEntry(_CatalogEntry):
    """One dimension, measure or network entry: just its "provides" note."""

    provides: str


class Catalog(_CatalogEntry):
    """The full API capability catalog: params, dimensions, measures, networks and limits."""

    search_params: dict[SearchParam, SearchParamEntry]
    dimensions: dict[Dimension, MenuEntry]
    measures: dict[Measure, MenuEntry]
    time_fields: list[TimeField]
    networks: dict[NetworkType, MenuEntry]
    limits: list[str]

    def render_for_planner(self) -> str:
        """Compact planner text: a static prefix so OpenAI's prompt caching applies (§5.10)."""
        sections = [
            _render_search_params(self.search_params),
            _render_menu("Dimensions (group_by / series_by / color_by)", self.dimensions),
            _render_menu("Measures (numeric)", self.measures),
            "## Time fields\n" + ", ".join(f.value for f in self.time_fields),
            _render_menu("Networks", self.networks),
            "## Limits\n" + "\n".join(f"- {limit}" for limit in self.limits),
        ]
        return "\n\n".join(sections)


def _render_search_params(params: dict[SearchParam, SearchParamEntry]) -> str:
    """Render every `query.*` param with its provides/use_for/avoid_for notes."""
    lines = ["## Search parameters (query.*)"]
    for param, entry in params.items():
        use = ", ".join(entry.use_for) or "-"
        avoid = ", ".join(entry.avoid_for) or "-"
        lines.append(
            f"- {param.value}: provides: {entry.provides} use_for: {use} avoid_for: {avoid}"
        )
    return "\n".join(lines)


def _render_menu(heading: str, entries: dict[Any, MenuEntry]) -> str:
    """Render one flat `name: provides` menu section."""
    lines = [f"## {heading}"]
    lines += [f"- {key.value}: {entry.provides}" for key, entry in entries.items()]
    return "\n".join(lines)


@lru_cache(maxsize=1)
def load_catalog(path: Path = CATALOG_PATH) -> Catalog:
    """Parse and validate `catalog.yaml`; cached since the file never changes at runtime."""
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return Catalog.model_validate(data)
