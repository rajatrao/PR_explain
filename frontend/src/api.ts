import type { RunDetail, RunSummary } from "./types";

export async function listRuns(): Promise<RunSummary[]> {
  const response = await fetch("/api/runs");
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function getRun(id: string): Promise<RunDetail> {
  const response = await fetch(`/api/runs/${id}`);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

export async function retryRun(id: string): Promise<RunDetail> {
  const response = await fetch(`/api/runs/${id}/retry`, { method: "POST" });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
