import { useEffect, useState } from "react";
import { listRuns } from "./api";
import { RunPage } from "./RunPage";
import type { RunSummary } from "./types";

function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return path;
}

export function App() {
  const path = usePath();
  const match = path.match(/^\/runs\/([^/]+)/);
  return (
    <div className="shell">
      <header className="top">
        <a className="word" href="/">
          PR Explain
        </a>
      </header>
      {match ? <RunPage id={decodeURIComponent(match[1])} /> : <Home />}
    </div>
  );
}

function Home() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    listRuns().then(setRuns).catch((err: Error) => setError(err.message));
  }, []);
  if (error) return <p className="error">{error}</p>;
  if (!runs) return <p>Loading runs…</p>;
  if (runs.length === 0) {
    return <p>No runs yet. A pull request webhook queues analysis, and this page reads the result.</p>;
  }
  return (
    <ul className="run-list">
      {runs.map((run) => (
        <li key={run.id}>
          <a href={`/runs/${run.id}`}>
            <strong>
              {run.repository} #{run.pr_number}
            </strong>
            <div>{run.title || "Untitled pull request"}</div>
            <div className="sha">
              {run.head_sha.slice(0, 12)} · analysis {run.analysis_status} · explanation {run.explanation_status}
            </div>
          </a>
        </li>
      ))}
    </ul>
  );
}
