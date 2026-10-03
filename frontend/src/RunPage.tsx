import { Fragment, useEffect, useRef, useState } from "react";
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
  return COVERAGE_LABEL[value] ?? `Coverage ${value.replaceAll("_", " ")}`;
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
              <ChangeFlowDiagram diagram={run.change_flow_diagram ?? { sections: [], text: "" }} />
              <section className="narrative">
                <h2>Explain</h2>
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

function MermaidDiagram({ chart }: { chart: string }) {
  const host = useRef<HTMLDivElement>(null);
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
  }, [chart]);

  return (
    <section className="diagram" aria-label="Change diagram">
      {!chart ? (
        <p>No changed symbols in the change graph.</p>
      ) : error ? (
        <p className="error">{error}</p>
      ) : (
        <div ref={host} className="mermaid-host" />
      )}
    </section>
  );
}

const DETAILS_ORDER = [
  "High-level areas affected",
  "Key Changes",
  "Behavior Changes",
  "Risk Areas",
  "What changed",
  "Change flow",
  "Impact",
  "Shared code",
  "Tests",
  "Unchanged boundary",
  "Why a file outside the diff matters",
  "Unknowns",
];

const HIDDEN_ON_DETAILS = new Set(["Review questions", "Reviewer Attention"]);

const TABLE_HEADERS: Record<string, [string, string]> = {
  "High-level areas affected": ["Area", "Names"],
  "Key Changes": ["Change", "Location"],
  "Behavior Changes": ["Call", "Evidence"],
  "Risk Areas": ["Where", "Why look"],
  "Suggested review areas": ["Where", "Why look"],
};

function detailsSectionOrder<T extends { title: string }>(sections: T[]): T[] {
  const rank = new Map(DETAILS_ORDER.map((title, index) => [title, index]));
  return sections.slice().sort((a, b) => (rank.get(a.title) ?? 100) - (rank.get(b.title) ?? 100));
}

const WRAP_FIRST_COLUMN = new Set([
  "High-level areas affected",
  "Key Changes",
  "Behavior Changes",
  "Risk Areas",
  "What changed",
  "Impact",
  "Unchanged boundary",
  "Why a file outside the diff matters",
]);

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
  const visible = rows.filter(isDetailRow);
  const impact = title === "Impact";
  const headers = TABLE_HEADERS[title];
  const className = ["detail-group", wrapFirst ? "wrap-first" : "", impact ? "impact" : ""].filter(Boolean).join(" ");
  return (
    <div className={className}>
      <h3>{title}</h3>
      {visible.length === 0 ? (
        <p className="kicker">none found</p>
      ) : (
        <>
          {impact ? (
            <div className="detail-row detail-columns-head">
              <span className="detail-label">Area</span>
              <span>Reason</span>
              <span>Evidence file</span>
            </div>
          ) : headers ? (
            <div className="detail-row detail-columns-head">
              <span className="detail-label">{headers[0]}</span>
              <span>{headers[1]}</span>
            </div>
          ) : null}
          {visible.map((row, index) => (
            <div className="detail-row" key={`${title}-${row.label}-${row.value}-${index}`}>
              {impact ? (
                <ImpactCells row={row} />
              ) : (
                <>
                  <span className="detail-label">{row.label}</span>
                  {row.href ? (
                    <a href={row.href} target="_blank" rel="noreferrer">
                      {row.value}
                    </a>
                  ) : (
                    <span>{row.value}</span>
                  )}
                </>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function ImpactCells({ row }: { row: DetailRow }) {
  const evidence = row.evidence?.trim() || "";
  return (
    <>
      <span className="detail-label">{row.label}</span>
      <span>{row.value}</span>
      {evidence && row.href ? (
        <a className="detail-evidence" href={row.href} target="_blank" rel="noreferrer">
          {evidence}
        </a>
      ) : (
        <span className="detail-evidence">{evidence}</span>
      )}
    </>
  );
}

function ReviewQuestionsView({ run }: { run: RunDetail }) {
  const attention = sectionRows(run, "Reviewer Attention");
  const suggested = subsectionRows(run, "Reviewer Attention", "Suggested review areas");
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
        <DetailGroup title="Suggested review areas" rows={suggested} wrapFirst />
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

function subsectionRows(run: RunDetail, sectionTitle: string, subsectionTitle: string): DetailRow[] {
  const section = (run.details?.sections ?? []).find((item) => item.title === sectionTitle);
  const subsection = section?.subsections?.find((item) => item.title === subsectionTitle);
  if (!subsection) return [];
  return subsection.rows.filter(isDetailRow).filter((row) => row.value.trim() !== "none found");
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
    <section className="delta">
      <h2>What is new since {run.delta.previous_head_sha.slice(0, 12)}</h2>
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
    </section>
  );
}
