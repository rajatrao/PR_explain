import { useEffect, useState } from "react";
import { getRun, requestExplanation } from "./api";
import type { Claim, Epistemic, Evidence, ExplanationDocument, RunDetail, Statement } from "./types";

const SECTIONS: { title: string; key: keyof ExplanationDocument }[] = [
  { title: "Change flow", key: "change_flow" },
  { title: "Impacts", key: "impacts" },
  { title: "Important changes", key: "important_changes" },
  { title: "Tests", key: "tests" },
  { title: "Unchanged", key: "unchanged" },
  { title: "Unknowns", key: "unknowns" },
  { title: "Review questions", key: "review_questions" },
];

export function RunPage({ id }: { id: string }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [depth, setDepth] = useState("developer");
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
          next.explanation_status === "queued" ||
          next.explanation_status === "running" ||
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
  const document = explanation?.status === "succeeded" ? explanation.document : null;
  const developerFailed = run.explanation_status === "failed";

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
        <span className="chip">{run.language_coverage}</span>
      </div>
      <StageList events={run.events} />

      {developerFailed && (
        <div className="banner" role="status">
          <strong>Explanation failed.</strong>
          <p>{run.explanation_error || "The model did not return a validated document."}</p>
          <p>Files, symbols, and claims from the analysis are still here.</p>
          <button type="button" onClick={() => generate("developer")} disabled={busy}>
            Retry developer explanation
          </button>
        </div>
      )}
      {run.comment_status === "failed" && run.comment_error && (
        <p className="error">The pull request comment was not updated. {run.comment_error}</p>
      )}

      <div className="tabs">
        {run.depths.map((item) => (
          <button
            key={item}
            type="button"
            className={item === depth ? "tab active" : "tab"}
            onClick={() => setDepth(item)}
          >
            {item}
          </button>
        ))}
      </div>

      <div className="layout">
        <div>
          {document ? (
            <ExplanationView
              document={document}
              evidence={run.evidence}
              repo={run.revision.repository}
              sha={run.revision.head_sha}
            />
          ) : depth === "developer" && developerFailed ? null : (
            <section className="narrative">
              <p>
                {explanation?.status === "failed"
                  ? explanation.error || "This depth failed validation."
                  : `No ${depth} narration yet.`}
              </p>
              <button type="button" className="primary" onClick={() => generate(depth)} disabled={busy}>
                Generate {depth}
              </button>
            </section>
          )}
          <DeltaView run={run} />
          <ClaimsView claims={run.claims} evidence={run.evidence} repo={run.revision.repository} sha={run.revision.head_sha} />
        </div>
        <aside className="side">
          <section>
            <h2>Files</h2>
            {run.files.map((file) => (
              <div className="file" key={file.path}>
                <span>{file.path}</span>
                {file.changed ? <span className="changed">changed</span> : <span>not in diff</span>}
              </div>
            ))}
          </section>
          <section>
            <h2>Symbols</h2>
            {run.symbols.map((symbol) => (
              <div className="symbol" key={symbol.id}>
                {symbol.name}
                <span className="kicker">
                  {" "}
                  {symbol.file_path}:{symbol.start_line}
                  {symbol.changed ? " · changed" : ""}
                </span>
              </div>
            ))}
          </section>
          <section>
            <h2>Callers</h2>
            {run.relationships
              .filter((item) => item.type === "CALLS")
              .map((item) => (
                <div className="symbol" key={item.id}>
                  {item.source} → {item.target}
                </div>
              ))}
          </section>
        </aside>
      </div>
    </article>
  );
}

function StageList({ events }: { events: RunDetail["events"] }) {
  return (
    <section className="stages">
      <h2>Stages</h2>
      {events.length === 0 ? (
        <p className="kicker">No stages recorded yet.</p>
      ) : (
        <ol>
          {events.map((event, index) => (
            <li key={`${event.created_at}-${event.stage}-${index}`}>
              <span className={`chip ${event.status}`}>{event.status}</span>
              <span className="stage-name">{event.stage.replaceAll("_", " ")}</span>
              <span>{event.message}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function ExplanationView({
  document,
  evidence,
  repo,
  sha,
}: {
  document: ExplanationDocument;
  evidence: Evidence[];
  repo: string;
  sha: string;
}) {
  return (
    <section className="narrative">
      <p>{document.summary}</p>
      {SECTIONS.map((section) => {
        const statements = document[section.key];
        if (!Array.isArray(statements) || statements.length === 0) return null;
        return (
          <div key={section.key}>
            <h2>{section.title}</h2>
            {(statements as Statement[]).map((statement, index) => (
              <div className="statement" key={`${section.key}-${index}`}>
                <span className={`chip epistemic ${statement.epistemic}`}>{statement.epistemic}</span>
                <div>
                  <p>{statement.text}</p>
                  <EvidenceLinks ids={statement.evidence_ids} evidence={evidence} repo={repo} sha={sha} />
                </div>
              </div>
            ))}
          </div>
        );
      })}
    </section>
  );
}

function ClaimsView({
  claims,
  evidence,
  repo,
  sha,
}: {
  claims: Claim[];
  evidence: Evidence[];
  repo: string;
  sha: string;
}) {
  return (
    <section className="claims">
      <h2>Claims</h2>
      {claims.map((claim) => (
        <article className="claim" key={claim.id}>
          <header>
            <span className={`chip epistemic ${claim.epistemic}`}>{claim.epistemic}</span>
            <span className="kicker">{claim.kind}</span>
          </header>
          <p>{claim.text}</p>
          <EvidenceLinks ids={claim.evidence_ids} evidence={evidence} repo={repo} sha={sha} />
        </article>
      ))}
    </section>
  );
}

function EvidenceLinks({
  ids,
  evidence,
  repo,
  sha,
}: {
  ids: string[];
  evidence: Evidence[];
  repo: string;
  sha: string;
}) {
  const linked = ids
    .map((id) => evidence.find((item) => item.id === id))
    .filter((item): item is Evidence => Boolean(item && item.file));
  if (linked.length === 0) return null;
  return (
    <div className="links">
      {linked.map((item) => (
        <a key={item.id} href={blobUrl(repo, sha, item)} target="_blank" rel="noreferrer">
          {label(item)}
        </a>
      ))}
    </div>
  );
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

function blobUrl(repo: string, sha: string, item: Evidence) {
  const base = `https://github.com/${repo}/blob/${sha}/${item.file}`;
  if (item.start_line && item.end_line && item.end_line !== item.start_line) {
    return `${base}#L${item.start_line}-L${item.end_line}`;
  }
  if (item.start_line) return `${base}#L${item.start_line}`;
  return base;
}

function label(item: Evidence) {
  if (item.start_line) return `${item.file}:${item.start_line}`;
  return item.file || item.id;
}
