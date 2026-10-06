import { Fragment, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import mermaid from "mermaid";
import { getRun, requestExplanation, retryRun } from "./api";
import type {
  BehaviorFlows,
  ChangeFlowDiagram,
  FileChange,
  RunDetail,
  BehavioralChanges,
  ImpactSummary,
} from "./types";

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  theme: "neutral",
  flowchart: { htmlLabels: true, curve: "basis" },
});

const TABS = ["quick", "deep", "review"] as const;

type TabId = (typeof TABS)[number];

const TAB_LABEL: Record<TabId, string> = {
  quick: "Explain",
  deep: "Details",
  review: "Review",
};

const COVERAGE_LABEL: Record<string, string> = {
  ts: "Coverage TypeScript",
  diff_only: "Coverage Diff only",
};

function coverageLabel(value: string): string {
  const known = COVERAGE_LABEL[value];
  if (known) return known;
  const languages = value
    .split(",")
    .map((part) => part.trim().replaceAll("_", " "))
    .filter(Boolean);
  return languages.length ? `Coverage ${languages.join(", ")}` : "Coverage Diff only";
}

function githubTarget(run: RunDetail): { href: string; label: string } | null {
  const parts = run.revision.repository.split("/");
  if (parts.length !== 2) return null;
  const [owner, repo] = parts;
  const number = run.revision.pr_number;
  if (!owner || !repo || !number) return null;
  const href = `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/pull/${number}`;
  const commentId = run.comment_status === "posted" ? run.github_comment_id : null;
  if (commentId) {
    return { href: `${href}#issuecomment-${commentId}`, label: "GitHub comment" };
  }
  return { href, label: "GitHub pull request" };
}

export function RunPage({ id }: { id: string }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("quick");
  const [depth, setDepth] = useState("quick");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    async function load() {
      try {
        const next = await getRun(id);
        if (cancelled) return;
        setRun(next);
        const pending =
          next.analysis_status === "queued" ||
          next.analysis_status === "running" ||
          next.explanation_status === "queued" ||
          next.explanation_status === "running" ||
          next.comment_status === "queued" ||
          next.explanations[depth]?.status === "queued";
        if (pending) timer = window.setTimeout(load, 2000);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load the run");
      }
    }
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [id, depth, busy]);

  if (error) return <p className="error">{error}</p>;
  if (!run) return <p>Loading explanation…</p>;

  const explanation = run.explanations[depth];
  const failedPhase = failedRunPhase(run);
  const viewLabel = TAB_LABEL[depth as TabId] || depth;
  const github = githubTarget(run);

  async function retry() {
    setBusy(true);
    setError(null);
    try {
      setRun(await retryRun(run!.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not retry the run");
    } finally {
      setBusy(false);
    }
  }

  async function generate(nextDepth: string) {
    setBusy(true);
    setError(null);
    try {
      await requestExplanation(run!.id, nextDepth);
      setDepth(nextDepth);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not queue the explanation");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article>
      <p className="kicker">
        {run.revision.repository} #{run.revision.pr_number}
      </p>
      <h1>{run.revision.title || "Pull request"}</h1>
      {github && (
        <p className="github-link">
          <a href={github.href} target="_blank" rel="noreferrer">
            {github.label}
          </a>
        </p>
      )}
      <p className="sha">{run.revision.head_sha}</p>
      <div className="statuses">
        <span className={`chip ${run.analysis_status}`}>Analysis {run.analysis_status}</span>
        <span className={`chip ${run.explanation_status}`}>Explanation {run.explanation_status}</span>
        <span className={`chip ${run.comment_status}`}>Comment {run.comment_status}</span>
        <span className="chip coverage">{coverageLabel(run.language_coverage)}</span>
      </div>
      {failedPhase && (
        <div className="banner" role="status">
          <strong>{failedPhase.title}</strong>
          <p>{failedPhase.detail}</p>
          {failedPhase.phase === "explanation" && (
            <p>Files, symbols, and claims from the analysis are still here.</p>
          )}
          <button type="button" onClick={() => void retry()} disabled={busy}>
            Retry
          </button>
        </div>
      )}

      {run.comment_status === "failed" && run.comment_error && failedPhase?.phase !== "comment" && (
        <p className="error">The pull request comment was not updated. {run.comment_error}</p>
      )}

      <div className="tabs" role="tablist" aria-label="Explanation">
        {TABS.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={item === tab}
            className={item === tab ? "tab active" : "tab"}
            onClick={() => {
              setTab(item);
              if (item === "review") return;
              setDepth(item);
              const existing = run.explanations[item];
              const pending = existing?.status === "queued" || existing?.status === "running";
              if ((!existing || existing.status !== "succeeded" || !existing.document) && !pending) {
                void generate(item);
              }
            }}
          >
            {TAB_LABEL[item]}
          </button>
        ))}
      </div>

      <div className="layout solo">
        <div>
          {tab === "review" ? (
            <ReviewView run={run} />
          ) : run.analysis_status === "succeeded" && tab === "quick" ? (
            <>
              <MermaidDiagram chart={run.change_flow_diagram?.mermaid ?? ""} />
              {run.change_flow_diagram?.legend ? <p className="kicker diagram-legend">{run.change_flow_diagram.legend}</p> : null}
              <BehavioralChangesSection section={run.behavioral_changes} />
              <ImpactSection section={run.impact} />
            </>
          ) : run.analysis_status === "succeeded" && tab === "deep" ? (
            <DetailsView run={run} />
          ) : run.analysis_status === "failed" ? null : (
            <section className="narrative">
              <p>
                {explanation?.status === "failed"
                  ? explanation.error || "This depth failed validation."
                  : `No ${viewLabel} narration yet.`}
              </p>
              <button type="button" className="primary" onClick={() => generate(depth)} disabled={busy}>
                Generate {viewLabel}
              </button>
            </section>
          )}
        </div>
      </div>
    </article>
  );
}

const PHASE_STAGES = {
  analysis: ["snapshot_fetch", "diff_analysis", "symbol_analysis", "change_graph", "evidence", "impact", "claims_persisted", "explanation_packet_persisted"],
  explanation: ["explanation"],
  comment: ["comment"],
} as const;

function failedRunPhase(run: RunDetail): { phase: "analysis" | "explanation" | "comment"; title: string; detail: string } | null {
  const analysis = phaseFailure(
    run.analysis_status,
    run.analysis_error,
    unresolvedStageFailure(run, PHASE_STAGES.analysis),
    "The analysis did not finish.",
  );
  if (analysis) return { phase: "analysis", title: "Analysis failed.", detail: analysis };
  const quickStillFailed =
    run.explanations.quick?.status === "failed" &&
    run.explanation_status !== "queued" &&
    run.explanation_status !== "running"
      ? run.explanations.quick.error || "Explanation failed."
      : null;
  const explanation = phaseFailure(
    run.explanation_status,
    run.explanation_error || run.explanations.quick?.error,
    unresolvedStageFailure(run, PHASE_STAGES.explanation) || quickStillFailed,
    "The model did not return a validated document.",
  );
  if (explanation) return { phase: "explanation", title: "Explanation failed.", detail: explanation };
  const comment = phaseFailure(
    run.comment_status,
    run.comment_error,
    unresolvedStageFailure(run, PHASE_STAGES.comment),
    "The comment was not posted.",
  );
  if (comment) return { phase: "comment", title: "The pull request comment was not updated.", detail: comment };
  return null;
}

function phaseFailure(status: string, error: string | null | undefined, eventDetail: string | null, fallback: string): string | null {
  if (status === "succeeded" || status === "posted") return null;
  if (status === "failed") return error || eventDetail || fallback;
  if (eventDetail) return error || eventDetail;
  return null;
}

function unresolvedStageFailure(run: RunDetail, stages: readonly string[]): string | null {
  let message: string | null = null;
  let failedIndex = -1;
  run.events.forEach((event, index) => {
    if (!stages.includes(event.stage)) return;
    if (event.status === "failed") {
      message = event.message || "failed";
      failedIndex = index;
    } else if (event.status === "succeeded" || event.status === "posted") {
      message = null;
      failedIndex = -1;
    }
  });
  if (message === null || failedIndex < 0) return null;
  const retried = run.events.slice(failedIndex + 1).some((event) => event.stage === "job_queued");
  return retried ? null : message;
}

function ChangeFlowDiagram({ diagram }: { diagram: ChangeFlowDiagram }) {
  const sections = diagram.sections ?? [];
  return (
    <section className="flow" aria-label="Change flow">
      {sections.length === 0 ? (
        <p>No changed symbols in the change graph.</p>
      ) : (
        <ul className="flow-list">
          {sections.map((section, sectionIndex) => (
            <li key={`${section.heading}-${sectionIndex}`}>
              {section.heading}
              {section.items.length > 0 ? (
                <ul>
                  {section.items.map((item, index) => (
                    <li key={`${section.heading}-${item.text}-${index}`}>
                      {item.text}
                      {item.detail ? (
                        <ul>
                          <li className="flow-detail">{item.detail}</li>
                        </ul>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

type DetailRow = {
  label: string;
  value: string;
  href?: string | null;
  label_href?: string | null;
  evidence?: string | null;
};

const DIAGRAM_ZOOM_MIN = 0.5;
const DIAGRAM_ZOOM_MAX = 2.5;
const DIAGRAM_ZOOM_STEP = 0.25;
const DIAGRAM_ZOOM_DEFAULT = 1;

function useMermaidHost(chart: string, host: RefObject<HTMLDivElement | null>) {
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!chart || !host.current) return;
    let cancelled = false;
    const id = `explain-${Math.random().toString(36).slice(2, 10)}`;
    mermaid
      .render(id, chart)
      .then(({ svg }) => {
        if (!cancelled && host.current) {
          host.current.innerHTML = svg;
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setError("The diagram could not be drawn.");
      });
    return () => {
      cancelled = true;
    };
  }, [chart, host]);

  return error;
}

function MermaidDiagram({
  chart,
  label = "Change diagram",
  emptyText = "No changed symbols in the change graph.",
}: {
  chart: string;
  label?: string;
  emptyText?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const modalHost = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(DIAGRAM_ZOOM_DEFAULT);
  const [expanded, setExpanded] = useState(false);
  const error = useMermaidHost(chart, host);
  const modalError = useMermaidHost(expanded ? chart : "", modalHost);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const zoomIn = () => setZoom((value) => Math.min(DIAGRAM_ZOOM_MAX, value + DIAGRAM_ZOOM_STEP));
  const zoomOut = () => setZoom((value) => Math.max(DIAGRAM_ZOOM_MIN, value - DIAGRAM_ZOOM_STEP));
  const resetZoom = () => setZoom(DIAGRAM_ZOOM_DEFAULT);

  return (
    <section className="diagram" aria-label={label}>
      {!chart ? (
        <p>{emptyText}</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : (
        <>
          <div className="diagram-toolbar" role="toolbar" aria-label="Diagram controls">
            <button
              type="button"
              className="diagram-control diagram-control-icon"
              onClick={zoomOut}
              disabled={zoom <= DIAGRAM_ZOOM_MIN}
              aria-label="Zoom out"
              title="Zoom out"
            >
              −
            </button>
            <button
              type="button"
              className="diagram-control diagram-control-icon"
              onClick={zoomIn}
              disabled={zoom >= DIAGRAM_ZOOM_MAX}
              aria-label="Zoom in"
              title="Zoom in"
            >
              +
            </button>
            <button
              type="button"
              className="diagram-control"
              onClick={resetZoom}
              disabled={zoom === DIAGRAM_ZOOM_DEFAULT}
              aria-label="Reset"
              title="Reset"
            >
              Reset
            </button>
            <button
              type="button"
              className="diagram-control diagram-control-icon"
              onClick={() => setExpanded(true)}
              aria-label="Open larger"
              title="Open larger"
            >
              ⛶
            </button>
          </div>
          <div className="mermaid-zoom-viewport">
            <div className="mermaid-host" ref={host} style={{ zoom }} />
          </div>
        </>
      )}
      {expanded && chart && !error ? (
        <div className="diagram-overlay" role="dialog" aria-modal="true" aria-label={`${label} enlarged`}>
          <div className="diagram-modal">
            <div className="diagram-modal-head">
              <button type="button" className="diagram-control" onClick={() => setExpanded(false)}>
                Close
              </button>
            </div>
            {modalError ? (
              <p className="error">{modalError}</p>
            ) : (
              <div className="mermaid-host mermaid-host-expanded" ref={modalHost} />
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}

const DETAILS_ORDER = [
  "High-level areas affected",
  "Key Changes",
  "Risk Areas",
  "What changed",
  "Shared code",
  "Callers outside the diff",
];

const HIDDEN_ON_DETAILS = new Set([
  "Review questions",
  "Reviewer Attention",
  "Unknowns",
  "Risk Areas",
  "What changed",
  "Tests",
  "Unchanged boundary",
]);

const DETAIL_ROW_LIMIT = 20;

const FOLD_SECTIONS = new Set<string>();

const TABLE_HEADERS: Record<string, [string, string]> = {
  "High-level areas affected": ["Area", "Names"],
  "Key Changes": ["Function", "What it means for callers"],
  "Risk Areas": ["Where", "Why look"],
};

function detailsSectionOrder<T extends { title: string }>(sections: T[]): T[] {
  const rank = new Map(DETAILS_ORDER.map((title, index) => [title, index]));
  return sections.slice().sort((a, b) => (rank.get(a.title) ?? 100) - (rank.get(b.title) ?? 100));
}

const WRAP_FIRST_COLUMN = new Set([
  "High-level areas affected",
  "Key Changes",
  "Risk Areas",
  "What changed",
  "Callers outside the diff",
]);

function ImpactSection({ section }: { section?: ImpactSummary }) {
  if (!section) return null;
  const areas = section.areas ?? [];
  return (
    <section className="narrative impact-section">
      <h3>Impact</h3>
      {section.overview ? (
        <p className="behavior-summary">
          <InlineCode text={section.overview} />
        </p>
      ) : null}
      {areas.map((area) => (
        <div className="behavior-card" key={area.title}>
          <h4 className="behavior-title">
            <span className={`severity severity-${area.severity}`}>{area.severity}</span> <InlineCode text={area.title} />
          </h4>
          <p className="impact-why">
            <InlineCode text={area.summary} />
          </p>
          {area.who_notices ? (
            <p className="behavior-impact">
              <span className="detail-label">Who notices</span> <InlineCode text={area.who_notices} />
            </p>
          ) : null}
        </div>
      ))}
      {areas.length ? (
        <p className="kicker">
          Written by the configured model from rule-derived impact findings; each item was checked against the facts it
          cites.
        </p>
      ) : null}
    </section>
  );
}

function BehaviorFlowsSection({ flows }: { flows?: BehaviorFlows }) {
  if (!flows || (!flows.before && !flows.after)) return null;
  return (
    <section className="narrative behavior-flows">
      <h3>Old flow vs New flow</h3>
      {flows.legend ? <p className="kicker">{flows.legend}</p> : null}
      <div className="flow-pair">
        <div className="flow-side flow-before">
          <h4>Old flow (base)</h4>
          <MermaidDiagram chart={flows.before} label="Old flow diagram" emptyText="Nothing to draw for the base commit." />
        </div>
        <div className="flow-side flow-after">
          <h4>New flow (head)</h4>
          <MermaidDiagram chart={flows.after} label="New flow diagram" emptyText="The changed code is not present at the head commit." />
        </div>
      </div>
    </section>
  );
}

/** Render `code` spans inside a sentence without interpreting any other markup. */
function InlineCode({ text }: { text: string }) {
  const parts = text.split(/(``\s.+?\s``|`[^`]+`)/g);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith("`` ") && part.endsWith(" ``")) return <code key={index}>{part.slice(3, -3)}</code>;
        if (part.length > 1 && part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
        return <Fragment key={index}>{part}</Fragment>;
      })}
    </>
  );
}

function BehavioralChangesSection({ section }: { section?: BehavioralChanges }) {
  if (!section) return null;
  const changes = section.changes ?? [];
  return (
    <section className="narrative behavioral-changes">
      <h3>Behavioral Changes</h3>
      {section.overview ? (
        <p className="behavior-summary">
          <InlineCode text={section.overview} />
        </p>
      ) : null}
      {changes.length === 0 && !section.overview ? <p>The diff shows no statement-level behavior change.</p> : null}
      {changes.map((change, index) => (
        <div className="behavior-card" key={`${index}-${change.title}`}>
          <h4 className="behavior-title">
            <InlineCode text={change.title} />
          </h4>
          <div className="behavior-compare">
            <div className="behavior-before">
              <span className="detail-label">Before</span>
              <p>
                <InlineCode text={change.before} />
              </p>
            </div>
            <div className="behavior-after">
              <span className="detail-label">After</span>
              <p>
                <InlineCode text={change.after} />
              </p>
            </div>
          </div>
          {change.impact ? (
            <p className="behavior-impact">
              <span className="detail-label">Who notices</span> <InlineCode text={change.impact} />
            </p>
          ) : null}
        </div>
      ))}
      {section.watch?.length ? (
        <div className="flow-focus">
          <h4>Worth checking</h4>
          <ul>
            {section.watch.map((item) => (
              <li key={item}>
                <InlineCode text={item} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {changes.length ? (
        <p className="kicker">
          Written by the configured model from {section.fact_count} before-and-after facts in the diff; each item was
          checked against the facts it cites.
        </p>
      ) : null}
    </section>
  );
}

function DetailsView({ run }: { run: RunDetail }) {
  const sections = detailsSectionOrder(run.details?.sections ?? []).filter(
    (section) => !HIDDEN_ON_DETAILS.has(section.title),
  );
  const changes = run.changes ?? [];
  const hasWhatChanged = sections.some((section) => section.title === "What changed");
  const hasKeyChanges = sections.some((section) => section.title === "Key Changes");
  const flows = <BehaviorFlowsSection flows={run.behavior_flows} />;
  return (
    <section className="narrative">
      {hasKeyChanges ? null : flows}
      {sections.length === 0 && changes.length === 0 ? (
        <p className="kicker">none found</p>
      ) : (
        <>
          {sections.map((section) => (
            <Fragment key={section.title}>
              {section.title === "Key Changes" ? flows : null}
              <DetailGroup
                title={section.title}
                rows={section.rows}
                wrapFirst={WRAP_FIRST_COLUMN.has(section.title)}
              />
              {section.title === "What changed" ? <FileChanges changes={changes} /> : null}
            </Fragment>
          ))}
          {hasWhatChanged ? null : <FileChanges changes={changes} />}
        </>
      )}
    </section>
  );
}

function FileChanges({ changes }: { changes: FileChange[] }) {
  if (changes.length === 0) return null;
  return (
    <div className="detail-group file-changes">
      <h3>Changes</h3>
      {changes.map((file) => (
        <details className="file-change" key={file.path}>
          <summary>{file.path}</summary>
          <pre>{file.diff}</pre>
        </details>
      ))}
    </div>
  );
}

function isDetailRow(row: DetailRow | null): row is DetailRow {
  return Boolean(row && row.label && row.value);
}

function DetailGroup({ title, rows, wrapFirst = false }: { title: string; rows: DetailRow[]; wrapFirst?: boolean }) {
  const visible = rows.filter(isDetailRow).filter((row) => !isOmittedEmptyRow(title, row));
  if (title === "Risk Areas" && visible.length === 0) return null;
  const folded = FOLD_SECTIONS.has(title);
  const shown = folded ? visible : visible.slice(0, DETAIL_ROW_LIMIT);
  const extra = folded ? [] : visible.slice(DETAIL_ROW_LIMIT);
  const headers = TABLE_HEADERS[title];
  const className = ["detail-group", wrapFirst ? "wrap-first" : ""].filter(Boolean).join(" ");
  const body = (
    <>
      {visible.length === 0 ? (
        <p className="kicker">none found</p>
      ) : (
        <>
          {headers ? (
            <div className="detail-row detail-columns-head">
              <span className="detail-label">{headers[0]}</span>
              <span>{headers[1]}</span>
            </div>
          ) : null}
          <DetailRows title={title} rows={shown} />
          {extra.length > 0 ? (
            <details className="detail-more">
              <summary>Show {extra.length} more</summary>
              <DetailRows title={title} rows={extra} />
            </details>
          ) : null}
        </>
      )}
    </>
  );
  if (folded) {
    return (
      <details className={className}>
        <summary>{title}</summary>
        {body}
      </details>
    );
  }
  return (
    <div className={className}>
      <h3>{title}</h3>
      {body}
    </div>
  );
}

function isOmittedEmptyRow(title: string, row: DetailRow): boolean {
  if (title !== "Risk Areas") return false;
  return row.value.trim().toLowerCase() === "none found";
}

function DetailRows({ title, rows }: { title: string; rows: DetailRow[] }) {
  return (
    <>
      {rows.map((row, index) => (
        <div className="detail-row" key={`${title}-${row.label}-${row.value}-${index}`}>
          <span className="detail-label">
            {row.label_href ? (
              <a href={row.label_href} target="_blank" rel="noreferrer">
                {row.label}
              </a>
            ) : (
              row.label
            )}
          </span>
          {row.href ? (
            <a href={row.href} target="_blank" rel="noreferrer">
              {row.value}
            </a>
          ) : (
            <span>{row.value}</span>
          )}
        </div>
      ))}
    </>
  );
}

function ReviewView({ run }: { run: RunDetail }) {
  const report = run.review;
  if (!report) return <p className="kicker">The review is not available for this run yet.</p>;
  const link = (location: string) => codeLink(location, run.revision.repository, run.revision.head_sha);
  const tops = new Set(report.top_questions.map((item) => item.question));
  const moreQuestions = report.questions.filter((item) => !tops.has(item.question));
  const testGroups = groupBy(report.missing_tests, (item) => item.group);
  return (
    <div className="review">
      {report.source === "rules" ? (
        <p className="kicker">From the analysis rules only; no model review is stored for this run.</p>
      ) : null}

      <ReviewSection title="1. Reviewer attention areas" empty={report.attention.length === 0}>
        {report.attention.map((area, index) => (
          <article className="review-card" key={`${index}-${area.area}`}>
            <header>
              <span className={`priority priority-${area.priority.toLowerCase()}`}>{area.priority}</span>
              <strong>{area.area}</strong>
            </header>
            <dl>
              <dt>Why it matters</dt>
              <dd>{area.why_it_matters}</dd>
              <dt>What changed</dt>
              <dd>{area.what_changed}</dd>
              <dt>What could go wrong</dt>
              <dd>{area.what_could_go_wrong}</dd>
              {area.involved.length > 0 ? (
                <>
                  <dt>Files/functions</dt>
                  <dd>{joinNodes(area.involved.map(link))}</dd>
                </>
              ) : null}
            </dl>
          </article>
        ))}
      </ReviewSection>

      <ReviewSection title="2. Reviewer questions" empty={moreQuestions.length === 0 && report.top_questions.length === 0}>
        {moreQuestions.length > 0 ? (
          <ul className="review-questions">
            {moreQuestions.map((item, index) => (
              <li key={`${index}-${item.question}`}>{item.question}</li>
            ))}
          </ul>
        ) : (
          <p className="review-empty">All questions are ranked in the top questions below.</p>
        )}
      </ReviewSection>

      <ReviewSection title="3. Potential bugs and regressions" empty={report.bugs.length === 0}>
        {report.bugs.map((bug, index) => (
          <article className="review-card" key={`${index}-${bug.finding}`}>
            <header>
              <span className={`priority status-${bug.status}`}>{bug.status}</span>
              <strong>{bug.finding}</strong>
              <span className="kicker"> confidence {bug.confidence}</span>
            </header>
            <dl>
              <dt>Evidence</dt>
              <dd>
                {bug.evidence}
                {bug.locations.length > 0 ? <> ({joinNodes(bug.locations.map(link))})</> : null}
              </dd>
              <dt>Scenario</dt>
              <dd>{bug.scenario}</dd>
              <dt>Impact</dt>
              <dd>{bug.impact}</dd>
            </dl>
          </article>
        ))}
      </ReviewSection>

      <ReviewSection title="4. Missing test scenarios" empty={report.missing_tests.length === 0}>
        {[...testGroups.entries()].map(([group, tests]) => (
          <div className="detail-group" key={group}>
            <h3>{group}</h3>
            <ul className="review-questions">
              {tests.map((test, index) => (
                <li key={`${index}-${test.scenario}`}>
                  {test.scenario} <span className="kicker">— {test.verifies}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </ReviewSection>

      <ReviewSection title="5. Things that look safe" empty={report.safe.length === 0}>
        <ul className="review-questions">
          {report.safe.map((item, index) => (
            <li key={`${index}-${item.area}`}>
              <strong>{item.area}</strong> — {item.why}
            </li>
          ))}
        </ul>
      </ReviewSection>

      <ReviewSection title="6. Top review questions" empty={report.top_questions.length === 0}>
        <ol className="review-questions ranked">
          {report.top_questions.map((item, index) => (
            <li key={`${index}-${item.question}`}>
              <strong>{item.question}</strong>
              <div className="kicker">Why ask this: {item.why_ask}</div>
              <div className="kicker">
                Relevant code: {item.locations.length > 0 ? joinNodes(item.locations.map(link)) : item.relevant_code}
              </div>
            </li>
          ))}
        </ol>
      </ReviewSection>

      {report.undetermined.length > 0 ? (
        <ReviewSection title="Could not determine">
          <ul className="review-questions">
            {report.undetermined.map((text, index) => (
              <li key={`${index}-${text}`}>{text}</li>
            ))}
          </ul>
        </ReviewSection>
      ) : null}

      <section className={`review-panel risk risk-${report.overall_risk.toLowerCase()}`} aria-label="Overall review risk">
        <h2>
          Overall review risk: <span className="risk-badge">{report.overall_risk}</span>
        </h2>
        <p>{report.risk_reason}</p>
      </section>
    </div>
  );
}

function ReviewSection({ title, empty = false, children }: { title: string; empty?: boolean; children?: ReactNode }) {
  return (
    <section className="review-panel" aria-label={title}>
      <h2>{title}</h2>
      {empty ? <p className="review-empty">None found in the diff and stored facts.</p> : children}
    </section>
  );
}

function codeLink(location: string, repo: string, sha: string): ReactNode {
  const match = /^([^\s:]+?)(?::(\d+)(?:-(\d+))?)?$/.exec(location);
  if (!match || !match[1].includes(".")) return <code key={location}>{location}</code>;
  const [, path, start, end] = match;
  const anchor = start ? `#L${start}${end ? `-L${end}` : ""}` : "";
  return (
    <a key={location} href={`https://github.com/${repo}/blob/${sha}/${path}${anchor}`} target="_blank" rel="noreferrer">
      <code>{location}</code>
    </a>
  );
}

function joinNodes(nodes: ReactNode[]): ReactNode[] {
  return nodes.flatMap((node, index) => (index === 0 ? [node] : [", ", node]));
}

function groupBy<T>(items: T[], key: (item: T) => string): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const item of items) {
    const name = key(item);
    out.set(name, [...(out.get(name) ?? []), item]);
  }
  return out;
}
