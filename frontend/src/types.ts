export type Epistemic = "FACT" | "INFERENCE" | "UNKNOWN";

export type Statement = {
  epistemic: Epistemic;
  text: string;
  claim_ids: string[];
  evidence_ids: string[];
};

export type ExplanationDocument = {
  summary: string;
  change_flow: Statement[];
  impacts: Statement[];
  important_changes: Statement[];
  tests: Statement[];
  unchanged: Statement[];
  unknowns: Statement[];
  review_questions: Statement[];
};

export type Evidence = {
  id: string;
  type: string;
  repo: string;
  commit_sha: string;
  file: string | null;
  start_line: number | null;
  end_line: number | null;
  symbol: string | null;
  description: string;
};

export type Claim = {
  id: string;
  epistemic: Epistemic;
  kind: string;
  text: string;
  subject: string | null;
  evidence_ids: string[];
};

export type SymbolRow = {
  id: string;
  name: string;
  kind: string;
  file_path: string;
  start_line: number;
  end_line: number;
  exported: boolean;
  changed: boolean;
};

export type RunSummary = {
  id: string;
  analysis_status: string;
  explanation_status: string;
  comment_status: string;
  language_coverage: string;
  repository: string;
  pr_number: number;
  head_sha: string;
  title: string | null;
};

export type RunEvent = {
  stage: string;
  status: string;
  message: string;
  detail: Record<string, string | number | boolean> | null;
  created_at: string | null;
  head_sha: string | null;
};

export type Delta = {
  previous_head_sha: string | null;
  added: { text: string; epistemic: Epistemic; kind: string }[];
  removed: { text: string; epistemic: Epistemic; kind: string }[];
  unchanged_count: number;
};

export type ChangeFlowItem = {
  text: string;
  detail: string | null;
};

export type ChangeFlowSection = {
  heading: string;
  items: ChangeFlowItem[];
};

export type ChangeFlowDiagram = {
  sections: ChangeFlowSection[];
  text: string;
  mermaid?: string;
  legend?: string;
};

export type FileChange = {
  path: string;
  diff: string;
};

export type RunDetail = {
  id: string;
  analysis_status: string;
  explanation_status: string;
  comment_status: string;
  github_comment_id: number | null;
  analysis_error: string | null;
  explanation_error: string | null;
  comment_error: string | null;
  language_coverage: string;
  revision: {
    id: string;
    repository: string;
    pr_number: number;
    head_sha: string;
    base_sha: string;
    title: string | null;
    body: string | null;
  };
  files: { path: string; changed: boolean }[];
  symbols: SymbolRow[];
  relationships: { id: string; type: string; source: string; target: string; source_file: string | null }[];
  claims: Claim[];
  evidence: Evidence[];
  explanations: Record<
    string,
    {
      depth: string;
      status: string;
      provider: string | null;
      model: string | null;
      error: string | null;
      document: ExplanationDocument | null;
    }
  >;
  delta: Delta | null;
  events: RunEvent[];
  depths: string[];
  change_flow_diagram: ChangeFlowDiagram;
  explain_bullets: string[];
  behavioral_changes?: BehavioralChanges;
  impact?: ImpactSummary;
  changes?: FileChange[];
  review?: ReviewReport;
  details?: {
    sections: {
      title: string;
      rows: {
        label: string;
        value: string;
        href?: string | null;
        label_href?: string | null;
        evidence?: string | null;
      }[];
      subsections?: {
        title: string;
        rows: { label: string; value: string; href?: string | null; evidence?: string | null }[];
      }[];
    }[];
  };
};

export type BehavioralChanges = {
  source: "model" | "none";
  fact_count: number;
  overview: string;
  changes: { title: string; before: string; after: string; impact: string; evidence?: EvidenceLink[] }[];
  watch: string[];
};


export type ImpactSummary = {
  source: "model" | "none";
  overview: string;
  areas: {
    title: string;
    severity: "high" | "medium" | "low";
    summary: string;
    who_notices: string;
    evidence?: EvidenceLink[];
  }[];
};


export type ReviewPriority = "Critical" | "High" | "Medium" | "Low";

type Grounded = { fact_ids: string[]; locations: string[]; source?: string };

export type ReviewReport = {
  source?: "stored" | "rules";
  attention: (Grounded & {
    area: string;
    why_it_matters: string;
    what_changed: string;
    what_could_go_wrong: string;
    involved: string[];
    priority: ReviewPriority;
  })[];
  bugs: (Grounded & {
    finding: string;
    evidence: string;
    scenario: string;
    impact: string;
    confidence: "High" | "Medium" | "Low";
    status: "confirmed" | "possible";
  })[];
  top_questions: (Grounded & { question: string; why_ask: string; relevant_code: string })[];
  overall_risk: ReviewPriority;
  risk_reason: string;
  risk_drivers?: {
    text: string;
    level: ReviewPriority;
    location?: string | null;
    fact_ids: string[];
    source: "rules" | "model";
  }[];
};

export type EvidenceLink = { label: string; href?: string | null };
