"""Visualization schemas: the eight chart shapes the response can carry (§12.4-§12.5)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ctviz.schemas.citations import Citation

Row = dict[str, Any]


class Channel(BaseModel):
    """One Vega-Lite-style encoding channel: which data field maps to which visual role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    type: Literal["quantitative", "nominal", "ordinal", "temporal"]
    title: str | None = None
    unit: Literal["trials", "participants", "months", "share"] | None = None
    format: str | None = None
    time_unit: Literal["year", "month"] | None = None
    sort: Literal["ascending", "descending"] | list[str] | None = None
    bin: bool | None = None


class _Viz(BaseModel):
    """Shared shape for every visualization type: a title, required encoding, and options."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    encoding: dict[str, Channel]
    options: dict[str, Any] = Field(default_factory=dict)


class BarChart(_Viz):
    """A single-cohort categorical count (§12.4)."""

    type: Literal["bar_chart"]
    data: list[Row]


class GroupedBarChart(_Viz):
    """A categorical count compared across cohorts, in long/tidy rows (§12.4)."""

    type: Literal["grouped_bar_chart"]
    data: list[Row]


class TimeSeries(_Viz):
    """A count bucketed by time, optionally split by cohort (§12.4)."""

    type: Literal["time_series"]
    data: list[Row]


class Histogram(_Viz):
    """A binned distribution of a numeric measure (§12.4)."""

    type: Literal["histogram"]
    data: list[Row]


class ScatterPlot(_Viz):
    """One point per trial across two numeric measures (§12.4)."""

    type: Literal["scatter_plot"]
    data: list[Row]


class Table(_Viz):
    """A row-per-trial or row-per-bucket listing (§12.4)."""

    type: Literal["table"]
    data: list[Row]


class Metric(_Viz):
    """One or more single headline numbers, always as an array (§12.4)."""

    type: Literal["metric"]
    data: list[Row]


class NetworkNode(BaseModel):
    """One node (sponsor/drug/condition) in a network_graph, with its own citation evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    type: Literal["sponsor", "drug", "condition"]
    weight: int = Field(ge=0)
    predicate: dict[str, Any]
    citations: list[Citation]


class NetworkEdge(BaseModel):
    """One edge linking two node ids, with its own citation evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source: str
    target: str
    type: Literal["sponsor_drug", "drug_drug", "condition_drug"]
    weight: int = Field(ge=0)
    predicate: dict[str, Any]
    citations: list[Citation]
    flags: list[str] = Field(default_factory=list)


class NetworkData(BaseModel):
    """The full node/edge graph payload for a network_graph visualization."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directed: bool
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]

    @model_validator(mode="after")
    def _edges_join_known_nodes(self) -> "NetworkData":
        """Reject an edge whose source or target id isn't in `nodes` (referential integrity)."""
        ids = {node.id for node in self.nodes}
        bad = [e.id for e in self.edges if e.source not in ids or e.target not in ids]
        if bad:
            raise ValueError(f"edges reference unknown node ids: {bad}")
        return self


class NetworkGraph(_Viz):
    """A node/edge relationship graph, e.g. sponsors linked to the drugs they test (§12.5)."""

    type: Literal["network_graph"]
    data: NetworkData


Visualization = Annotated[
    BarChart
    | GroupedBarChart
    | TimeSeries
    | Histogram
    | ScatterPlot
    | NetworkGraph
    | Table
    | Metric,
    Field(discriminator="type"),
]
