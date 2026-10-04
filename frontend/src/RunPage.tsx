import { Fragment, useEffect, useRef, useState, type RefObject } from "react";
import mermaid from "mermaid";
import { getRun, requestExplanation, retryRun } from "./api";
import type { ChangeFlowDiagram, Epistemic, FileChange, RunDetail } from "./types";

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
            <ReviewQuestionsView run={run} />
          ) : run.analysis_status === "succeeded" && tab === "quick" ? (
            <>
              <MermaidDiagram chart={run.change_flow_diagram?.mermaid ?? ""} />
              <BehavioralChangesSection rows={run.behavioral_changes ?? []} />
              <LabeledExplainSection title="System Impact" rows={run.system_impact ?? []} />
              <section className="narrative">
                <h2>Summary</h2>
                <ul className="bullets">
                  {(run.explain_bullets ?? []).map((item, index) => (
                    <li key={`${index}-${item}`}>{item}</li>
                  ))}
                </ul>
              </section>
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
          {tab === "deep" ? <DeltaView run={run} /> : null}
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

function MermaidDiagram({ chart }: { chart: string }) {
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
    <section className="diagram" aria-label="Change diagram">
      {!chart ? (
        <p>No changed symbols in the change graph.</p>
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
        <div className="diagram-overlay" role="dialog" aria-modal="true" aria-label="Change diagram enlarged">
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
  "Change flow",
  "Shared code",
  "Why a file outside the diff matters",
  "Tests",
  "Unchanged boundary",
];

const HIDDEN_ON_DETAILS = new Set(["Review questions", "Reviewer Attention", "Unknowns"]);

const DETAIL_ROW_LIMIT = 20;

const FOLD_SECTIONS = new Set(["Tests", "Unchanged boundary"]);

const TABLE_HEADERS: Record<string, [string, string]> = {
  "High-level areas affected": ["Area", "Names"],
  "Key Changes": ["Change", "Location"],
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
  "Unchanged boundary",
  "Why a file outside the diff matters",
]);

function LabeledExplainSection({ title, rows }: { title: string; rows: { label: string; value: string }[] }) {
  if (rows.length === 0) return null;
  return (
    <section className="narrative detail-group wrap-first">
      <h3>{title}</h3>
      {rows.map((row, index) => (
        <div className="detail-row" key={`${title}-${row.label}-${index}`}>
          <span className="detail-label">{row.label}</span>
          <span>{row.value}</span>
        </div>
      ))}
    </section>
  );
}

function BehavioralChangesSection({ rows }: { rows: { label: string; value: string }[] }) {
  return <LabeledExplainSection title="Behavioral Changes" rows={rows} />;
}

function DetailsView({ run }: { run: RunDetail }) {
  const sections = detailsSectionOrder(run.details?.sections ?? []).filter(
    (section) => !HIDDEN_ON_DETAILS.has(section.title),
  );
  const changes = run.changes ?? [];
  const hasWhatChanged = sections.some((section) => section.title === "What changed");
  return (
    <section className="narrative">
      {sections.length === 0 && changes.length === 0 ? (
        <p className="kicker">none found</p>
      ) : (
        <>
          {sections.map((section) => (
            <Fragment key={section.title}>
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
          <span className="detail-label">{row.label}</span>
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

function ReviewQuestionsView({ run }: { run: RunDetail }) {
  const attention = sectionRows(run, "Reviewer Attention");
  const questions = reviewQuestionTexts(run);
  return (
    <>
      <section className="review-panel" aria-label="Reviewer Attention">
        <h2>Reviewer Attention</h2>
        {attention.length === 0 ? (
          <p className="review-empty">none were found</p>
        ) : (
          <ul className="review-questions">
            {attention.map((row, index) => (
              <li key={`${index}-${row.label}-${row.value}`}>
                <span className="detail-label">{row.label}</span>
                {" — "}
                {row.href ? (
                  <a href={row.href} target="_blank" rel="noreferrer">
                    {row.value}
                  </a>
                ) : (
                  row.value
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="review-panel" aria-label="Review questions">
        <h2>Review questions</h2>
        {questions.length === 0 ? (
          <p className="review-empty">none were found</p>
        ) : (
          <ul className="review-questions">
            {questions.map((question, index) => (
              <li key={`${index}-${question}`}>{question}</li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function sectionRows(run: RunDetail, title: string): DetailRow[] {
  const section = (run.details?.sections ?? []).find((item) => item.title === title);
  if (!section) return [];
  const seen = new Set<string>();
  const rows: DetailRow[] = [];
  for (const row of section.rows) {
    const value = row.value.trim();
    if (!row.label || !value || value === "none found" || seen.has(`${row.label}\0${value}`)) continue;
    seen.add(`${row.label}\0${value}`);
    rows.push(row);
  }
  return rows;
}

function reviewQuestionTexts(run: RunDetail): string[] {
  const section = (run.details?.sections ?? []).find((item) => item.title === "Review questions");
  if (!section) return [];
  const seen = new Set<string>();
  const texts: string[] = [];
  for (const row of section.rows) {
    const value = row.value.trim();
    if (!value || value === "none found" || seen.has(value)) continue;
    seen.add(value);
    texts.push(value);
  }
  return texts;
}

function DeltaView({ run }: { run: RunDetail }) {
  if (!run.delta || !run.delta.previous_head_sha) return null;
  return (
    <details className="delta">
      <summary>What is new since {run.delta.previous_head_sha.slice(0, 12)}</summary>
      <p className="kicker">{run.delta.unchanged_count} claims unchanged</p>
      {run.delta.added.map((claim) => (
        <article className="claim" key={`add-${claim.text}`}>
          <header>
            <span className={`chip epistemic ${claim.epistemic as Epistemic}`}>{claim.epistemic}</span>
            <span className="kicker">added</span>
          </header>
          <p>{claim.text}</p>
        </article>
      ))}
      {run.delta.removed.map((claim) => (
        <article className="claim" key={`rem-${claim.text}`}>
          <header>
            <span className="chip">removed</span>
          </header>
          <p>{claim.text}</p>
        </article>
      ))}
    </details>
  );
}
