# Plan Overview — ClinicalTrials.gov Query-to-Visualization Agent

> **The short version: 15 pages, about 15 minutes.** Each page covers one idea: a diagram plus the few points that matter. The full plan (`PLAN.pdf`) has every number, schema and rule behind these pages. **Date:** 2026-09-28 · **Time box:** ~24h · **Stack:** Python 3.12 · FastAPI · Pydantic v2 · OpenAI + OpenRouter
>
> Colors used throughout: <span class="swatch llm">LLM</span> <span class="swatch code">deterministic code</span> <span class="swatch ext">ClinicalTrials.gov API</span> <span class="swatch io">input / output</span> <span class="swatch err">error outcome</span>

## Table of Contents

<div class="toc"><div class="toc-col"><ol class="toc-plain"><li><a href="#v0--one-page-summary">V0 · One-page summary</a></li><li><a href="#v1--the-system-in-one-picture">V1 · The system in one picture</a></li><li><a href="#v2--one-request-end-to-end">V2 · One request, end to end</a></li><li><a href="#v3--the-revise-loop-every-planning-outcome">V3 · The revise loop (every planning outcome)</a></li><li><a href="#v4--what-the-planner-is-allowed-to-say-queryplan">V4 · What the planner is allowed to say (QueryPlan)</a></li><li><a href="#v5--from-question-shape-to-chart">V5 · From question shape to chart</a></li><li><a href="#v6--the-eight-chart-types">V6 · The eight chart types</a></li><li><a href="#v7--where-every-trial-goes-the-data-funnel">V7 · Where every trial goes (the data funnel)</a></li></ol></div><div class="toc-col"><ol class="toc-plain"><li><a href="#v8--anatomy-of-a-deep-citation">V8 · Anatomy of a deep citation</a></li><li><a href="#v9--the-verifier-trust-but-recount">V9 · The verifier: trust, but recount</a></li><li><a href="#v10--what-the-response-looks-like">V10 · What the response looks like</a></li><li><a href="#v11--how-the-code-is-organized">V11 · How the code is organized</a></li><li><a href="#v12--the-24-hour-build-plan">V12 · The 24-hour build plan</a></li><li><a href="#v13--what-questions-it-answers">V13 · What questions it answers</a></li><li><a href="#v14--four-decisions-for-you">V14 · Four decisions for you</a></li></ol></div></div>

> **The one rule behind every design choice:** *LLMs decide **what to ask**; code decides **what is true**.*

## V0 · One-page summary

| | |
|---|---|
| **What** | A FastAPI backend that turns a natural-language clinical-trial question (plus optional fields) into a JSON **visualization spec** backed by ClinicalTrials.gov, with a **citation for every trial behind every datum**. |
| **How** | OpenAI **plans**, choosing from menus only. Code **checks** the plan and **probes** real counts. OpenRouter **judges** the plan. Then code **fetches, counts, cites and verifies**. |
| **Why it's trustworthy** | No number, category label or excerpt is written by an LLM. An independent verifier recounts every datum and refuses to answer if anything is off. |
| **Coverage** | 8 chart types (bar, grouped bar, time series, histogram, scatter, network, table, metric) and 19 query types (18 core + 1 stretch), covering every class in the assignment's appendix. API totals were measured live for 17 of them. |
| **Schedule** | About 24 h. A working end-to-end slice by hour 6.5, deep citations by hour 13.5, and 2.5 h reserved. The deliverables phase is protected. |
| **What I need from you** | Four quick decisions (page V14). |

**The one rule behind every design choice:** *LLMs decide **what to ask**; code decides **what is true**.*

## V1 · The system in one picture

```mermaid
%%{init: {"flowchart": {"rankSpacing": 24, "nodeSpacing": 24}}}%%
flowchart TB
    U(["User · question + optional fields"]):::io
    subgraph AI["LLM layer — decides WHAT TO ASK"]
        PL["<b>Planner</b><br/>OpenAI gpt-5.4-mini<br/>reads the API catalog"]:::llm
        JD["<b>Judge</b><br/>OpenRouter<br/>Gemini 2.5 Flash-Lite<br/>7-point intent check"]:::llm
    end
    subgraph CODE["Deterministic code — decides WHAT IS TRUE"]
        OV["<b>Overlay + checks</b><br/>fields applied · rules"]:::code
        PR["<b>Probe</b><br/>1-record count"]:::code
        EX["<b>Fetch + normalize</b><br/>+ strict match"]:::code
        AG["<b>Aggregate + cite</b><br/>counts with evidence"]:::code
        VF["<b>Verifier</b><br/>recount every datum"]:::code
    end
    CT[("ClinicalTrials.gov API v2")]:::ext
    OUT(["JSON visualization spec + deep citations"]):::io
    U --> PL
    U -. "NCT lookup (no LLM)" .-> EX
    PL -- "typed QueryPlan" --> OV
    OV --> PR
    PR -- "counts" --> JD
    JD -- "pass · or unavailable / flagged" --> EX
    OV -. "revise once" .-> PL
    PR -.-> PL
    JD -.-> PL
    EX --> AG --> VF --> OUT
    PR <-.-> CT
    EX <-.-> CT
    classDef llm fill:#efe7fb,stroke:#6a3d99,color:#2b1747
    classDef code fill:#e3f1f5,stroke:#0f5e7a,color:#0b3240
    classDef ext fill:#f1f1f1,stroke:#8a8a8a,color:#333
    classDef io fill:#e7f5ea,stroke:#17613a,color:#0e3a22
```

- **The LLMs only produce a typed plan:** menu choices plus entity names such as "pembrolizumab". They never write URLs, numbers or excerpts.
- **The plan is tested three ways before any real data is fetched:** code checks, a live count probe, and a judge from a *different* model family.
- **Any of the three checks can send the plan back, but only once** (dotted arrows), so latency is bounded: typically 3–9 s.
- **Everything after the judge is plain, testable Python.**

## V2 · One request, end to end

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant P as Planner (OpenAI)
    participant K as Checks + Probe
    participant J as Judge (OpenRouter)
    participant G as ClinicalTrials<br/>.gov
    participant E as Engine + Verifier
    C->>A: POST /v1/visualize {query, drug_name}
    A->>P: question + API capability catalog
    P-->>A: QueryPlan (menus only)
    A->>K: apply fields, validate plan
    K->>G: probe (pageSize=1, countTotal)
    G-->>K: totalCount = 2,960
    A->>J: plan + probe totals
    J-->>A: pass (7 checks)
    A->>E: execute plan
    E->>G: fetch all pages (≤ 20k)
    G-->>E: 2,960 records (3 pages)
    E->>E: normalize → strict match → aggregate + cite → verify
    E-->>A: spec + citations (verified)
    A-->>C: {visualization, meta} (gzip)
```

- The example is the assignment's own request: *"How has the number of trials for this drug changed over time?"* with `drug_name = Pembrolizumab`.
- **Typical timings:** planner 1–3 s · probe < 0.5 s · judge 0.5–1.5 s · fetch ~3–4 s for 2,960 records · verify < 0.5 s.
- **LLM cost is about $0.01 per request.**

## V3 · The revise loop (every planning outcome)

```mermaid
%%{init: {"flowchart": {"rankSpacing": 30, "nodeSpacing": 22}}}%%
flowchart TB
    P1["<b>Plan · attempt 1</b>"]:::llm --> C1("Checks"):::code
    C1 -- ok --> R1("Probe"):::code
    R1 -- "totals ok" --> J1("Judge"):::llm
    J1 -- "pass / unavailable" --> EX1(["EXECUTE<br/>passed · unavailable"]):::io
    C1 -- "fail" --> P2
    R1 -- "0 results / too broad" --> P2
    J1 -- "critical / major issue" --> P2
    P2["<b>Plan · attempt 2</b><br/>with the feedback"]:::llm --> C2("Checks"):::code
    C2 -- ok --> R2("Probe"):::code
    R2 -- "totals ok · or too broad (disclosed)" --> J2("Judge"):::llm
    J2 -- "pass / unavailable" --> EX2(["EXECUTE<br/>passed_after_revision · unavailable"]):::io
    J2 -- "still disagrees" --> EXF(["EXECUTE, flagged<br/>rejected_after_revision"]):::io
    R2 -- "0 results (every cohort)" --> NM(["NO_MATCHING_TRIALS"]):::err
    C2 -- "fail" --> F2("plan 1 passed<br/>checks + probe?"):::code
    F2 -- "yes" --> EXP(["EXECUTE plan 1<br/>executed_previous_plan"]):::io
    F2 -- "no" --> PI(["PLAN_INVALID"]):::err
    P1 -. "not answerable" .-> OOS(["OUT_OF_SCOPE<br/>judge skipped"]):::err
    P2 -. "not answerable" .-> OOS
    P1 -. "unreachable" .-> LU(["503 LLM_UNAVAILABLE"]):::err
    P2 -.-> LU
    classDef llm fill:#efe7fb,stroke:#6a3d99,color:#2b1747
    classDef code fill:#e3f1f5,stroke:#0f5e7a,color:#0b3240
    classDef io fill:#e7f5ea,stroke:#17613a,color:#0e3a22
    classDef err fill:#fdecea,stroke:#b3261e,color:#5c1410
```

- **At most 2 planner calls + 2 judge calls.** The loop can't spin.
- **Every ending is named.** Executed answers carry `meta.validation.judge.status` (passed · passed_after_revision · rejected_after_revision · executed_previous_plan · unavailable). The rest return `ok:false` with an `error.code` (OUT_OF_SCOPE · PLAN_INVALID · NO_MATCHING_TRIALS · LLM_UNAVAILABLE).
- **The judge can't block forever:** if it still disagrees after the revision, the answer ships *flagged*, with its objections attached.
- **If the judge is unreachable, the plan still runs (fail open), flagged `unavailable`.** Correctness is protected by the code checks, not by the judge.
- **Simple NCT-ID lookups bypass this loop entirely** (`judge.status = skipped`).

## V4 · What the planner is allowed to say (QueryPlan)

```mermaid
classDiagram
    direction LR
    class QueryPlan {
        answerable : bool
        out_of_scope_reason : text?
        suggested_reframing : text?
        interpretation : text
        assumptions : list
    }
    class SearchTerm {
        param : query.cond / intr / lead / spons / …
        value : entity text
        source : query_text / structured_field
        rationale : why this param
    }
    class EnumFilters {
        phases · statuses · study types
        intervention types · sponsor classes
        countries · start years · NCT IDs
    }
    class Comparison {
        vary_param
        values : 2 to 4
    }
    class Analysis {
        kind : count_by / time_trend / histogram / scatter / network / trial_lookup / trial_list
        group_by · time_field · measure · network_type
        top_n
    }
    class VizChoice {
        type : one of 8 chart types
        title : digit-guarded
        rationale : why this chart
    }
    QueryPlan *-- SearchTerm : 0..n
    QueryPlan *-- EnumFilters : 0..1
    QueryPlan *-- Comparison : 0..1
    QueryPlan *-- Analysis : 0..1
    QueryPlan *-- VizChoice : 0..1
```

- **Every search, filter, analysis and chart field is a menu choice or an entity name.** OpenAI's strict JSON-schema mode enforces this, then the §7.4 code checks. The only free text (title, interpretation, assumptions, rationales) is digit-guarded or shown for transparency, and is never used to query.
- **The `rationale` fields are the planner's reasoning** over the API catalog ("what does each call provide?"). The judge reads them.
- **Structured request fields (e.g. `drug_name`) are written in by code** afterwards, never copied by the LLM.

## V5 · From question shape to chart

```mermaid
flowchart LR
    subgraph Q["Question shape"]
        q1["…per year since 2015?"]
        q2["How are … distributed across phases?"]
        q4["Which countries have the most …?"]
        q3["Compare A vs B"]
        q5["Distribution of enrollment sizes"]
        q6["Enrollment vs duration"]
        q7["Network of sponsors ↔ drugs"]
        q8["Status of NCT04368728?"]
        q9["List recruiting Phase 3 trials for X"]
    end
    subgraph K["analysis.kind"]
        k1["time_trend"]
        k2["count_by"]
        k3["count_by + comparison"]
        k4["histogram"]
        k5["scatter"]
        k6["network"]
        k7["trial_lookup<br/>(simple lookups: no-LLM fast path)"]
        k8["trial_list"]
    end
    subgraph V["visualization.type"]
        v1["time_series"]
        v2["bar_chart"]
        v3["grouped_bar_chart"]
        v4["histogram"]
        v5["scatter_plot"]
        v6["network_graph"]
        v7["metric / table"]
        v8["table"]
    end
    q1 --> k1 --> v1
    q2 --> k2 --> v2
    q4 --> k2
    q3 --> k3 --> v3
    q5 --> k4 --> v4
    q6 --> k5 --> v5
    q7 --> k6 --> v6
    q8 --> k7 --> v7
    q9 --> k8 --> v8
    classDef default fill:#f7f9fb,stroke:#9aa7b8,color:#1d2433
```

- **The planner picks both the kind and the chart; code enforces that they're compatible.** For example, a `time_trend` can't become a network.
- **After the data arrives, deterministic *shape guards* adjust degenerate cases:**
  - 1 bucket → metric
  - < 3 years → bar chart
  - too many nodes → pruning
- **Simple NCT-ID lookups** (the IDs plus ≤ 6 words, with no 'by / vs / trend…') take a no-LLM fast path. Other NCT questions go to the planner with the IDs pre-filled.

## V6 · The eight chart types

<div class="cards">
<div class="card"><svg viewBox="0 0 120 64"><line x1="6" y1="58" x2="114" y2="58" stroke="#9aa7b8"/><rect x="12" y="20" width="14" height="38" fill="#0f5e7a"/><rect x="32" y="8" width="14" height="50" fill="#0f5e7a"/><rect x="52" y="30" width="14" height="28" fill="#0f5e7a"/><rect x="72" y="40" width="14" height="18" fill="#0f5e7a"/><rect x="92" y="48" width="14" height="10" fill="#0f5e7a"/></svg><div class="card-title">bar_chart</div><div class="card-enc">x: category · y: trial_count</div><div class="card-eg">Breast cancer trials by phase</div></div>
<div class="card"><svg viewBox="0 0 120 64"><line x1="6" y1="58" x2="114" y2="58" stroke="#9aa7b8"/><rect x="12" y="18" width="10" height="40" fill="#0f5e7a"/><rect x="23" y="28" width="10" height="30" fill="#c77d1a"/><rect x="44" y="10" width="10" height="48" fill="#0f5e7a"/><rect x="55" y="22" width="10" height="36" fill="#c77d1a"/><rect x="76" y="38" width="10" height="20" fill="#0f5e7a"/><rect x="87" y="44" width="10" height="14" fill="#c77d1a"/></svg><div class="card-title">grouped_bar_chart</div><div class="card-enc">x · y · color = cohort</div><div class="card-eg">Phases: pembrolizumab vs nivolumab</div></div>
<div class="card"><svg viewBox="0 0 120 64"><line x1="6" y1="58" x2="114" y2="58" stroke="#9aa7b8"/><polyline points="10,50 25,40 40,30 55,32 70,24 85,20 100,16" fill="none" stroke="#0f5e7a" stroke-width="2.5"/><polyline points="100,16 112,36" fill="none" stroke="#0f5e7a" stroke-width="2.5" stroke-dasharray="4 3"/></svg><div class="card-title">time_series</div><div class="card-enc">x: year · y: trial_count · partial year dashed</div><div class="card-eg">Pembrolizumab trials per year</div></div>
<div class="card"><svg viewBox="0 0 120 64"><line x1="6" y1="58" x2="114" y2="58" stroke="#9aa7b8"/><rect x="10" y="30" width="14" height="28" fill="#0f5e7a"/><rect x="24" y="12" width="14" height="46" fill="#0f5e7a"/><rect x="38" y="8" width="14" height="50" fill="#0f5e7a"/><rect x="52" y="20" width="14" height="38" fill="#0f5e7a"/><rect x="66" y="36" width="14" height="22" fill="#0f5e7a"/><rect x="80" y="50" width="14" height="8" fill="#0f5e7a"/><rect x="94" y="55" width="14" height="3" fill="#0f5e7a"/></svg><div class="card-title">histogram</div><div class="card-enc">bins: bin_start / bin_end · y: trial_count</div><div class="card-eg">Enrollment sizes, Phase 2 psoriasis</div></div>
<div class="card"><svg viewBox="0 0 120 64"><line x1="6" y1="58" x2="114" y2="58" stroke="#9aa7b8"/><line x1="8" y1="4" x2="8" y2="58" stroke="#9aa7b8"/><circle cx="20" cy="46" r="3" fill="#0f5e7a"/><circle cx="32" cy="40" r="3" fill="#0f5e7a"/><circle cx="41" cy="44" r="3" fill="#0f5e7a"/><circle cx="52" cy="30" r="3" fill="#0f5e7a"/><circle cx="60" cy="36" r="3" fill="#0f5e7a"/><circle cx="72" cy="22" r="3" fill="#0f5e7a"/><circle cx="84" cy="28" r="3" fill="#0f5e7a"/><circle cx="98" cy="12" r="3" fill="#0f5e7a"/></svg><div class="card-title">scatter_plot</div><div class="card-enc">one point = one trial · x · y</div><div class="card-eg">Enrollment vs duration, Crohn's P3</div></div>
<div class="card"><svg viewBox="0 0 120 64"><g stroke="#9aa7b8" stroke-width="1.5"><line x1="22" y1="16" x2="60" y2="32"/><line x1="22" y1="48" x2="60" y2="32"/><line x1="60" y1="32" x2="98" y2="14"/><line x1="60" y1="32" x2="98" y2="50"/><line x1="98" y1="14" x2="98" y2="50"/></g><circle cx="22" cy="16" r="7" fill="#c77d1a"/><circle cx="22" cy="48" r="5" fill="#c77d1a"/><circle cx="60" cy="32" r="9" fill="#0f5e7a"/><circle cx="98" cy="14" r="6" fill="#0f5e7a"/><circle cx="98" cy="50" r="5" fill="#0f5e7a"/></svg><div class="card-title">network_graph</div><div class="card-enc">nodes · edges · weight = shared trials</div><div class="card-eg">Sponsors ↔ drugs, glioblastoma</div></div>
<div class="card"><svg viewBox="0 0 120 64"><rect x="8" y="6" width="104" height="52" fill="none" stroke="#9aa7b8"/><rect x="8" y="6" width="104" height="12" fill="#eef2f6"/><line x1="8" y1="31" x2="112" y2="31" stroke="#d9dee7"/><line x1="8" y1="44" x2="112" y2="44" stroke="#d9dee7"/><line x1="44" y1="6" x2="44" y2="58" stroke="#d9dee7"/><line x1="80" y1="6" x2="80" y2="58" stroke="#d9dee7"/></svg><div class="card-title">table</div><div class="card-enc">columns[] · one row per item</div><div class="card-eg">Recruiting Phase 3 trials for X</div></div>
<div class="card"><svg viewBox="0 0 120 64"><text x="60" y="37" text-anchor="middle" font-size="16" font-weight="700" fill="#0f5e7a" font-family="Helvetica, Arial">COMPLETED</text><text x="60" y="54" text-anchor="middle" font-size="9" fill="#5b6475" font-family="Helvetica, Arial">NCT04368728 · status</text></svg><div class="card-title">metric</div><div class="card-enc">label · value · unit (data is always an array)</div><div class="card-eg">Status of NCT04368728?</div></div>
</div>

- **Every type has the same five keys:** `type`, `title`, `encoding`, `data`, `options`.
- **`encoding` names exactly which data field drives which visual channel,** so a frontend never guesses.
- **Data is already aggregated.** The frontend only draws; it never recounts.

## V7 · Where every trial goes (the data funnel)

```mermaid
flowchart TD
    A["<b>API totalCount</b><br/>2,960 pembrolizumab trials"]:::ext --> B["<b>records_fetched</b><br/>2,960 · 3 pages · ~3 s"]:::code
    B --> C["<b>Strict match?</b><br/>drug listed in an intervention field"]:::code
    C -- "no · 331 (11.2%)" --> X1["excluded · stage = match<br/>each NCT ID listed with reason"]:::err
    C -- "yes" --> D["<b>records_matched</b><br/>2,629"]:::code
    D --> E["<b>Has every field the chart needs?</b><br/>(e.g. start date for a trend)"]:::code
    E -- "no" --> X2["excluded · stage = analysis<br/>each NCT ID listed with reason"]:::err
    E -- "yes" --> F["<b>records_plotted</b><br/>= sum of the bars<br/>(exclusive dims: phase, year, status…)"]:::io
    classDef code fill:#e3f1f5,stroke:#0f5e7a,color:#0b3240
    classDef ext fill:#f1f1f1,stroke:#8a8a8a,color:#333
    classDef io fill:#e7f5ea,stroke:#17613a,color:#0e3a22
    classDef err fill:#fdecea,stroke:#b3261e,color:#5c1410
```

- **The API's text search is fuzzy:** it also matches trials that only *mention* the drug in a description. 331 of 2,960 pembrolizumab hits don't actually list it [VERIFIED].
- **Nothing disappears silently.** Every excluded trial is listed with its stage and reason in `meta.data_coverage`.
- **Over 20,000 matches?** The probe asks the planner to narrow the question first. Otherwise the most recent 20,000 are analyzed, and the response says so.

## V8 · Anatomy of a deep citation

```mermaid
flowchart LR
    D["<b>Datum</b><br/>bar: Phase 3<br/>trial_count = 324<br/>(pre-strict-match example)"]:::io --> P["<b>predicate</b><br/>phases = {PHASE3}"]:::code
    D --> C1["<b>Citation</b><br/>nct_id: NCT06472076<br/>field: …/phases/0<br/>excerpt: PHASE3"]:::code
    D --> C2["… one citation for<br/>every contributing trial"]:::code
    C1 --> E1["<b>evidence · match</b><br/>/…/interventions/0/name<br/>Pembrolizumab"]:::code
    C1 --> E2["<b>evidence · filter</b><br/>one item per filter<br/>(e.g. status, country)"]:::code
    C1 -. "JSON Pointer" .-> R[("Raw record<br/>GET /studies/NCT06472076")]:::ext
    E1 -. "JSON Pointer" .-> R
    classDef code fill:#e3f1f5,stroke:#0f5e7a,color:#0b3240
    classDef ext fill:#f1f1f1,stroke:#8a8a8a,color:#333
    classDef io fill:#e7f5ea,stroke:#17613a,color:#0e3a22
```

- Each citation answers two questions: **why is this trial in this bar** (`field`/`excerpt`), and **why is it in the chart at all** (`match` evidence).
- **Excerpts are exact values straight from the API**, one array element at a time, never paraphrased.
- **`trial_count` equals the number of citations**, so the bar height *is* its citation list.
- **Anyone can check a citation** with any JSON Pointer library, or with `python -m ctviz.verify`.

## V9 · The verifier: trust, but recount

```mermaid
flowchart TB
    IN(["Response + raw records"]):::io --> CHK
    subgraph CHK["Five independent checks"]
        direction TB
        V1["<b>1 · Pointer</b><br/>resolves; excerpt matches exactly"]:::code --> V2["<b>2 · Soundness</b><br/>cited trial satisfies cohort + datum rules"]:::code
        V2 --> V3["<b>3 · Relevance</b><br/>evidence points at the field the rule uses"]:::code
        V3 --> V4["<b>4 · Completeness</b><br/>independent recount = cited set"]:::code
        V4 --> V5["<b>5 · Structure</b><br/>bars sum to plotted · edges join real nodes"]:::code
    end
    CHK -- "all pass" --> OK(["return response"]):::io
    CHK -. "any check fails" .-> FAIL(["<b>500 CITATION_CHECK_FAILED</b><br/>fail closed: it's our bug"]):::err
    classDef code fill:#e3f1f5,stroke:#0f5e7a,color:#0b3240
    classDef io fill:#e7f5ea,stroke:#17613a,color:#0e3a22
    classDef err fill:#fdecea,stroke:#b3261e,color:#5c1410
```

- **The verifier never imports the counting code.** It re-derives every bar from the raw records using each datum's predicate, so a bucketing bug can't hide.
- **It catches the failure the first prototype missed:** a Phase 1 trial filed under the Phase 3 bar.
- **Nine deliberate-corruption tests prove it fails when it should.**

## V10 · What the response looks like

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 14}}}%%
flowchart LR
    R(["Response"]):::io --> VIZ["<b>visualization</b>"]:::code
    R --> META["<b>meta</b>"]:::code
    R --> OKF["schema_version · ok"]:::code
    OKF -- "ok = false" --> ERR["<b>error</b><br/>code · message · details"]:::err
    VIZ --> T["type · title"]
    VIZ --> ENC["encoding<br/>x · y · color … / node_* · edge_*"]
    VIZ --> DAT["data<br/>rows · bins · points · nodes + edges"]
    VIZ --> OPT["options<br/>stacked · scale · layout …"]
    DAT --> CIT["every datum:<br/>predicate + citations[]"]
    META --> M1["filters · interpretation<br/>assumptions · plan"]
    META --> M6["grouping · sort · units<br/>(time granularity, order)"]
    META --> M2["data_coverage · cohorts · overlap<br/>citation_policy · citation_check"]
    META --> M4["validation · probe + judge + trace<br/>provenance · API URLs · data_timestamp"]
    META --> M7["warnings · adjustments<br/>entity_resolution · network_summary · timing"]
    classDef default fill:#f7f9fb,stroke:#9aa7b8,color:#1d2433
    classDef code fill:#e3f1f5,stroke:#0f5e7a,color:#0b3240
    classDef io fill:#e7f5ea,stroke:#17613a,color:#0e3a22
    classDef err fill:#fdecea,stroke:#b3261e,color:#5c1410
```

- **One parse path for any frontend:** check `ok`, then render `visualization` using `encoding`.
- **`meta` explains the answer:** what was asked, which trials were counted or dropped and why, what the judge said, and which exact API calls produced the data.

## V11 · How the code is organized

```mermaid
flowchart TB
    API["api/<br/>routes · errors · replay mode"]:::io --> PIPE["pipeline.py"]:::code
    PIPE --> AGENT["agent/<br/>planner · overlay<br/>checks · judge<br/>orchestrator"]:::llm
    PIPE --> CTGOV["ctgov/<br/>fields · compiler<br/>probe · client<br/>normalize"]:::code
    PIPE --> ANALYSIS["analysis/<br/>dimensions<br/>aggregate · numeric<br/>network · guards"]:::code
    PIPE --> VIZ["viz/<br/>builder"]:::code
    PIPE --> CIT["citations/<br/>pointer · match<br/>predicates<br/>verify"]:::code
    AGENT --> LLM["llm/<br/>OpenAI + OpenRouter clients"]:::llm
    AGENT --> CAT["catalog/<br/>catalog.yaml · enums"]:::code
    AGENT --> CTGOV
    ANALYSIS --> COMMON["common/names<br/>(shared normalizers)"]:::code
    CIT --> COMMON
    CTGOV --> EXT[("ClinicalTrials.gov")]:::ext
    SCH["schemas/<br/>typed contracts<br/>used everywhere"]:::io
    classDef llm fill:#efe7fb,stroke:#6a3d99,color:#2b1747
    classDef code fill:#e3f1f5,stroke:#0f5e7a,color:#0b3240
    classDef ext fill:#f1f1f1,stroke:#8a8a8a,color:#333
    classDef io fill:#e7f5ea,stroke:#17613a,color:#0e3a22
```

- **Small typed modules** (~200–400 lines each), with one-way dependencies.
- **`citations/` never imports `analysis/`.** That independence is what makes the verifier meaningful.
- **LLM code is isolated in `agent/` + `llm/`.** Everything else is testable offline with recorded API fixtures.

## V12 · The 24-hour build plan

```mermaid
gantt
    dateFormat HH:mm
    axisFormat %H:%M
    todayMarker off
    section Foundation
    P0 Setup + smoke tests   :p0, 00:00, 30m
    P1 Contracts             :p1, after p0, 2h
    P2 Data layer + probe    :p2, after p1, 150m
    section Slice
    P2.5 End-to-end slice    :crit, p25, after p2, 90m
    section Core
    P3 Analysis + evidence   :p3, after p25, 4h
    P4 Citations + verifier  :p4, after p3, 3h
    P5 Agent + judge         :p5, after p4, 3h
    P6 API + replay mode     :p6, after p5, 1h
    section Ship
    P7 Evals                 :p7, after p6, 90m
    P8 Deliverables          :crit, p8, after p7, 150m
    Reserve                  :done, rs, after p8, 150m
```

- **A real end-to-end answer exists by hour 6.5**, so problems surface early.
- **The 2.5 h reserve covers a short review after each phase, plus slack.**
- **If behind at hour 16, cut in this order:** stretch items → judge eval size → condition↔drug network → scatter → table lists. **P8 (deliverables) is never cut.**
- **Stretch, only after P8:** site and investigator networks, then the demo UI (only if ≥ 2 h remain).

## V13 · What questions it answers

```mermaid
%%{init: {"themeVariables": {"cScale0":"#d7e8ee","cScaleLabel0":"#0b3240","cScale1":"#d7e8ee","cScaleLabel1":"#0b3240","cScale2":"#d7e8ee","cScaleLabel2":"#0b3240","cScale3":"#d7e8ee","cScaleLabel3":"#0b3240","cScale4":"#d7e8ee","cScaleLabel4":"#0b3240","cScale5":"#d7e8ee","cScaleLabel5":"#0b3240","cScale6":"#d7e8ee","cScaleLabel6":"#0b3240","cScale7":"#d7e8ee","cScaleLabel7":"#0b3240","cScale8":"#d7e8ee","cScaleLabel8":"#0b3240","cScale9":"#d7e8ee","cScaleLabel9":"#0b3240","cScale10":"#d7e8ee","cScaleLabel10":"#0b3240","cScale11":"#d7e8ee","cScaleLabel11":"#0b3240"}}}%%
mindmap
  root((One pipeline))
    Time trends
      Pembrolizumab per year
      Alzheimer's starts per year
    Distributions
      Breast cancer by phase
      T2D intervention types
      Merck Phase 3 status
    Comparisons
      Pembrolizumab vs nivolumab
      Lung cancer vs melanoma sponsor classes
    Geography
      MS recruiting countries
    Networks
      Sponsor ↔ drug
      Drug ↔ drug combos
      Condition ↔ drug
    Numeric
      Enrollment histogram
      Enrollment vs duration
    Edge cases
      NCT lookup, no LLM
      Out of scope
      Typos, zero results
      Too broad → most recent 20k
```

- **18 core query types (+1 stretch: the site network) run through the same pipeline**, with no per-question code. API totals for 17 were measured live (full plan §14); the Pfizer condition↔drug total is still an [ASSUMPTION].
- **Every class in the assignment's appendix is covered,** including the unscoped *"Which drugs frequently co-occur in combination studies?"*.

## V14 · Four decisions for you

| # | Question | My recommendation |
|---|---|---|
| **Q1** | Exclude trials the API matched only by *mentioning* the drug or sponsor in passing (11% for pembrolizumab)? | **Yes for drugs and sponsors.** Conditions stay lenient until P2 measures their match rate (switch to strict if ≥ 97%). Every excluded trial is listed. |
| **Q2** | "Merck" matches two unrelated companies (MSD and Merck KGaA). Warn, or auto-split? | **Warn and show the matched names**, without guessing. |
| **Q3** | Build a small HTML demo viewer? | **Only if ≥ 2 h remain** after the deliverables are done. |
| **Q4** | Should a "Phase 2/Phase 3" trial be its own bar (sums to the total, like ClinicalTrials.gov) or count in both? | **Its own bar.** (Counting in both stays available as stretch S5.) |

Answers are needed before P2 / P4, so P0–P1 can start right away. Reply with your answers, or "go with your recommendations".
