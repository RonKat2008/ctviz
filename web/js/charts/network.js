// Vega force-directed layout for network_graph. `static: true` computes the layout up front (no
// animated simulation), which keeps it deterministic and motion-free. Datum indices (`__i`) follow
// `datums(viz)` in specs.js: nodes first, then edges.

const VEGA_SCHEMA = "https://vega.github.io/schema/vega/v5.json";
const NODE_TYPES = ["sponsor", "drug", "condition"];
const LABEL_TOP_N = 10;

export function networkSpec(viz, t, opts = {}) {
  const { nodes, edges } = viz.data;
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const ranked = [...nodes].sort((a, b) => b.weight - a.weight).slice(0, opts.compact ? 3 : LABEL_TOP_N).map((n) => n.id);
  const labelled = new Set(ranked);
  const nodeValues = nodes.map((n, i) => ({ __i: i, id: n.id, label: n.label, type: n.type, weight: n.weight, __cites: n.citations.length, __label: labelled.has(n.id) }));
  const linkValues = edges.map((e, j) => ({ __i: nodes.length + j, source: index.get(e.source), target: index.get(e.target), weight: e.weight, __cites: e.citations.length, __name: `${nodes[index.get(e.source)].label} → ${nodes[index.get(e.target)].label}` }));
  const width = Math.max(260, Math.round(opts.width || 720));
  const height = opts.compact ? 190 : Math.round(Math.min(560, Math.max(360, width * 0.62)));
  const presentTypes = NODE_TYPES.filter((k) => nodes.some((n) => n.type === k));
  const fontSize = opts.compact ? 8 : 10.5;
  const labelEncode = (halo) => ({
    enter: {
      text: { field: "datum.label" },
      x: { field: "x" }, y: { field: "y" },
      dy: { signal: `-sqrt(datum.size) / 2 - 4` },
      align: { value: "center" }, baseline: { value: "bottom" },
      font: { value: t.fontMono }, fontSize: { value: fontSize },
      fill: { value: halo ? t.surface : t.ink },
      ...(halo ? { stroke: { value: t.surface }, strokeWidth: { value: 3 }, strokeJoin: { value: "round" } } : {}),
    },
    update: { opacity: { signal: "datum.datum.__label ? (sel < 0 || datum.datum.__i === sel ? 1 : 0.35) : 0" } },
  });
  return {
    $schema: VEGA_SCHEMA,
    width, height, padding: 6, autosize: "none", background: null,
    signals: [
      { name: "cx", update: "width / 2" },
      { name: "cy", update: "height / 2" },
      { name: "sel", value: -1 },
    ],
    data: [
      { name: "node-data", values: nodeValues },
      { name: "link-data", values: linkValues },
    ],
    scales: [
      { name: "color", type: "ordinal", domain: NODE_TYPES, range: [t.series[0], t.series[1], t.series[2]] },
      { name: "size", type: "sqrt", domain: { data: "node-data", field: "weight" }, range: opts.compact ? [12, 160] : [40, 700], zero: false },
      { name: "edgeWidth", type: "linear", domain: { data: "link-data", field: "weight" }, range: [0.75, opts.compact ? 2.5 : 5], zero: false },
    ],
    legends: opts.compact ? [] : [{
      fill: "color", orient: "top-left", direction: "horizontal", symbolType: "circle", title: null,
      values: presentTypes, labelFont: t.fontMono, labelColor: t.ink2, labelFontSize: 11,
      encode: { labels: { update: { text: { signal: "upper(slice(datum.label, 0, 1)) + slice(datum.label, 1)" } } } },
    }],
    marks: [
      {
        type: "symbol", name: "nodes", zindex: 1, from: { data: "node-data" },
        encode: {
          enter: { fill: { scale: "color", field: "type" }, stroke: { value: t.surface }, strokeWidth: { value: 2 }, size: { scale: "size", field: "weight" }, cursor: { value: "pointer" } },
          update: {
            opacity: { signal: "sel < 0 || datum.__i === sel ? 1 : 0.35" },
            tooltip: { signal: "{title: datum.label, Type: datum.type, Trials: format(datum.weight, ',~f'), Citations: format(datum.__cites, ',~f')}" },
          },
        },
        transform: [{
          type: "force", iterations: 300, static: true, signal: "force",
          forces: [
            { force: "center", x: { signal: "cx" }, y: { signal: "cy" } },
            { force: "collide", radius: { expr: "sqrt(datum.size) / 2 + 6" } },
            { force: "nbody", strength: opts.compact ? -30 : -140 },
            { force: "link", links: "link-data", distance: opts.compact ? 30 : 70 },
          ],
        }],
      },
      {
        type: "path", name: "links", from: { data: "link-data" }, interactive: true, zindex: 0,
        encode: {
          enter: { stroke: { value: t.edge }, strokeCap: { value: "round" }, cursor: { value: "pointer" }, strokeWidth: { scale: "edgeWidth", field: "weight" } },
          update: {
            strokeOpacity: { signal: "sel < 0 ? 0.55 : (datum.__i === sel || datum.source.datum.__i === sel || datum.target.datum.__i === sel ? 0.95 : 0.12)" },
            stroke: { signal: `datum.__i === sel ? '${t.cite}' : '${t.edge}'` },
            tooltip: { signal: "{title: datum.__name, 'Shared trials': format(datum.weight, ',~f'), Citations: format(datum.__cites, ',~f')}" },
          },
        },
        transform: [{ type: "linkpath", require: { signal: "force" }, shape: "line", sourceX: "datum.source.x", sourceY: "datum.source.y", targetX: "datum.target.x", targetY: "datum.target.y" }],
      },
      { type: "text", from: { data: "nodes" }, interactive: false, zindex: 2, encode: labelEncode(true) },
      { type: "text", from: { data: "nodes" }, interactive: false, zindex: 3, encode: labelEncode(false) },
    ],
  };
}
