// Example questions shown as exhibits. The first six are the offline replay questions in
// examples/canned_plans.json (tests/api/test_web.py keeps them in sync); the NCT lookup uses the
// key-facts fast path, which also works offline.

export const EXAMPLES = [
  { id: "A", kind: "Time series", query: "How has the number of pembrolizumab trials changed over time?" },
  { id: "B", kind: "Bar chart", query: "How many pembrolizumab trials are there by phase?" },
  { id: "C", kind: "Bar chart", query: "How many multiple sclerosis trials are recruiting, by country?" },
  { id: "D", kind: "Histogram", query: "What is the enrollment distribution for psoriasis phase 2 trials?" },
  { id: "E", kind: "Scatter plot", query: "Duration versus enrollment for completed phase 3 Crohn's disease trials" },
  { id: "F", kind: "Network graph", query: "Show the sponsor-drug network for glioblastoma trials" },
  { id: "G", kind: "Key facts", query: "Details of NCT02658279" },
];

export const TYPE_LABELS = {
  bar_chart: "Bar chart",
  grouped_bar_chart: "Grouped bar chart",
  time_series: "Time series",
  histogram: "Histogram",
  scatter_plot: "Scatter plot",
  network_graph: "Network graph",
  table: "Table",
  metric: "Metric",
};
