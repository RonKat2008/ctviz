"""Catalog loader tests: the YAML validates, and can't drift from the QueryPlan menus."""

from ctviz.catalog.loader import load_catalog
from ctviz.schemas.enums import Dimension, Measure, NetworkType, SearchParam


def test_catalog_lists_every_menu_value() -> None:
    catalog = load_catalog()

    assert set(catalog.search_params) == set(SearchParam)
    assert set(catalog.dimensions) == set(Dimension)
    assert set(catalog.measures) == set(Measure)
    assert set(catalog.networks) == set(NetworkType)


def test_render_for_planner_includes_params_and_their_provides_notes() -> None:
    catalog = load_catalog()

    text = catalog.render_for_planner()

    assert "query.intr" in text and "provides" in text.lower()


def test_render_for_planner_includes_every_dimension_measure_and_network() -> None:
    """Item 5: the planner-facing text must not silently drop a whole menu section."""
    catalog = load_catalog()

    text = catalog.render_for_planner()

    for dimension in Dimension:
        assert dimension.value in text
    for measure in Measure:
        assert measure.value in text
    for network in NetworkType:
        assert network.value in text
