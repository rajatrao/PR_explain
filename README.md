 # PR Explain

 **PR Explain** is an AI-assisted Pull Request explanation service that turns a GitHub Pull Request into an evidence-backed explanation of what changed, why it changed, and what parts of the codebase may be affected.

 The core idea is simple:

 > **PR Explain is like `SQL EXPLAIN` for Pull Requests.**

 Instead of asking an AI model to blindly read a Pull Request and guess what happened, PR Explain first performs deterministic code analysis, builds a structured change graph, collects evidence, and determines potential impact. The AI model then turns those facts into a human-readable explanation.
 
---

## Snapshot
1. List of Github PRs created
   <img width="845" height="858" alt="Screenshot 2026-10-06 at 12 48 49 PM" src="https://github.com/user-attachments/assets/1d6e1391-2317-4b69-b7a9-55de83261d64" />

2. PR explain view for quick look
   <img width="1134" height="1016" alt="Screenshot 2026-10-06 at 12 46 35 PM" src="https://github.com/user-attachments/assets/c58de7e4-4702-4f2e-bb62-7d69928e726c" />
   <img width="1111" height="783" alt="Screenshot 2026-10-06 at 12 51 09 PM" src="https://github.com/user-attachments/assets/11e4f75d-fd5d-4e54-a5de-713a96ca44a9" />
   <img width="1134" height="761" alt="Screenshot 2026-10-06 at 12 51 40 PM" src="https://github.com/user-attachments/assets/b2060cef-06ba-4216-805b-2f4b5377997f" />

3. PR details view
  <img width="1121" height="898" alt="Screenshot 2026-10-06 at 12 52 35 PM" src="https://github.com/user-attachments/assets/8794228a-93ac-42a2-9185-0a8451c05534" />
  <img width="1124" height="776" alt="Screenshot 2026-10-06 at 12 55 12 PM" src="https://github.com/user-attachments/assets/72d07c3c-dd84-48d1-9880-0173d2dcee10" />

4. PR review support
  <img width="1124" height="909" alt="Screenshot 2026-10-06 at 12 53 36 PM" src="https://github.com/user-attachments/assets/8dcf84e1-75c5-45dd-ae6a-0bf343a06f90" />
  <img width="1101" height="734" alt="Screenshot 2026-10-06 at 12 54 03 PM" src="https://github.com/user-attachments/assets/ca88a1fa-9d47-4ccd-a3a2-237acb715f34" />

  



---

 ## The `SQL EXPLAIN` Analogy

 If you've used `EXPLAIN` or `EXPLAIN ANALYZE` in SQL, the easiest way to understand PR Explain is to think of it as the same concept applied to code changes.

 For example:

```
EXPLAIN
SELECT *
FROM users
WHERE email = 'alice@example.com';
```

 The database doesn't ask an AI model to guess what the query does.

 Instead, the database analyzes the query and produces a structured execution plan describing things such as:

 - Which tables are accessed
- Which indexes may be used
- How operations are connected
- The expected execution strategy
- Potentially expensive operations

 PR Explain applies the same philosophy to Pull Requests.

 ### SQL

```
SQL Query
    │
    ▼
Query Planner
    │
    ▼
Execution Plan
    │
    ▼
Human Understanding
```

 ### PR Explain

```
Pull Request
    │
    ▼
Code Change Analyzer
    │
    ▼
Change Graph + Evidence + Impact
    │
    ▼
AI Explanation
    │
    ▼
Human Understanding
```

 The important distinction is:

 > **The AI is not the analyzer.**

 The deterministic analysis pipeline is responsible for discovering what actually changed.

 The AI model is responsible for explaining those discovered facts.

---

 ## Why This Matters

 A generic AI code-review system might look like:

```
Pull Request
     │
     ▼
    LLM
     │
     ▼
"Here's what I think changed..."
```

 PR Explain instead follows:

```
Pull Request
     │
     ▼
Deterministic Analysis
     │
     ├── Changed files
     ├── Changed symbols
     ├── Imports
     ├── Calls
     ├── Relationships
     ├── Evidence
     └── Impact
              │
              ▼
      Explanation Packet
              │
              ▼
          AI Model
              │
              ▼
     Human-readable explanation
```

 This makes the AI model primarily a **narrator**, rather than the source of truth.

 For example, if a Pull Request changes:

```
PaymentService.process_payment()
        │
        ├── PaymentRepository.create()
        │
        └── EventPublisher.publish()
```

 PR Explain first determines those relationships from the repository and Pull Request.

 The AI then receives the resulting evidence and can explain:

 > This change modifies payment processing and affects both persistence and event publishing. The new behavior therefore has potential impact on the payment data path as well as downstream consumers of the payment event.

 The model is explaining relationships that the analysis pipeline has already established rather than inventing them.

---

 # What PR Explain Does

 PR Explain analyzes Pull Requests and produces an explanation based on the actual repository contents and changes.

 The analysis includes:

 - Changed files
- Changed symbols
- Functions and classes
- Imports
- Call relationships
- Symbol relationships
- Relevant files outside the diff
- Line-level evidence
- Potential impact
- Claims about the change
- Revision-to-revision changes

 The resulting explanation can be viewed in the web application and posted back to the Pull Request as a GitHub comment.

---

 # Architecture

 ## End-to-End Flow

 A Pull Request moves through the following pipeline:

```
GitHub Pull Request
        │
        ▼
GitHub App Webhook
        │
        ▼
FastAPI API
        │
        ▼
Background Job
        │
        ▼
Worker
        │
        ├── Fetch repository snapshot
        ├── Analyze diff
        ├── Parse symbols
        ├── Build change graph
        ├── Collect evidence
        ├── Analyze impact
        └── Persist claims
                │
                ▼
        Explanation Packet
                │
                ▼
           AI Provider
                │
                ├───────────────┐
                │               │
                ▼               ▼
             Ollama           OpenAI
             Local            Production
                │               │
                └───────┬───────┘
                        ▼
                  Explanation
                        │
                        ▼
                   PostgreSQL
                    /       \
                   /         \
                  ▼           ▼
             React UI     GitHub API
                              │
                              ▼
                        PR Comment
```

---

 # Core Design Principle

 PR Explain follows:

 > **Deterministic analysis first. AI narration second.**

 The deterministic pipeline creates a structured representation of the change.

 The AI receives a bounded explanation packet containing those facts.

 This separation provides several benefits:

 - Better grounding
- Lower risk of hallucinated files or functions
- Repeatable analysis
- Inspectable evidence
- Local/private AI inference
- Ability to change AI providers without changing the analysis pipeline
- AI failures do not destroy deterministic analysis

---

 # GitHub App

 The GitHub App is the entry point into PR Explain.

 It receives Pull Request webhook events and gives PR Explain permission to read repository contents and update Pull Request comments.

 ## Required repository permissions

 | Permission | Access | Purpose |
| --- | --- | --- |
| Metadata | Read | Repository and installation metadata |
| Contents | Read | Read repository contents |
| Pull requests | Write | Create/update PR explanation comments |

PR Explain does not require GitHub Checks permissions and does not submit a Pull Request review score.

---

 # GitHub App Setup

 You need to create a GitHub App before PR Explain can receive Pull Request events.

 GitHub's official documentation:

 GitHub Apps Quickstart

 ## 1\. Create the GitHub App

 Go to:

```
GitHub
  → Settings
  → Developer settings
  → GitHub Apps
  → New GitHub App
```

 For an organization-owned application:

```
Organization
  → Settings
  → Developer settings
  → GitHub Apps
  → New GitHub App
```

 Use:

```
GitHub App name: PR Explain
```

 Configure the homepage URL to point to your deployed application or repository.

---

 # Webhook Configuration

 Enable:

```
Active: Yes
```

 Set the webhook URL to:

```
https://<your-public-host>/api/webhooks/github
```

 For local development, GitHub cannot directly reach `localhost`.

 Use a tunnel such as Cloudflare Tunnel:

```
cloudflared tunnel --url http://localhost:8000
```

 If the tunnel provides:

```
https://example.trycloudflare.com
```

 configure:

```
https://example.trycloudflare.com/api/webhooks/github
```

 as the GitHub App webhook URL.

 Only the FastAPI API needs to be exposed.

 **Do not expose Ollama publicly.**

---

 # Webhook Secret

 Generate a strong random secret and configure the same value in:

```
GITHUB_WEBHOOK_SECRET=your-secret
```

 The webhook secret is used to validate that incoming webhook requests originate from GitHub.

 Never commit the webhook secret to Git.

---

 # GitHub App Events

 Subscribe to:

 - `installation`
- `installation_repositories`
- `pull_request`

 For Pull Requests, PR Explain handles:

 - `opened`
- `reopened`
- `synchronize`
- `closed`

 The `closed` event is received but does not trigger analysis.

---

 # GitHub App Private Key

 From the GitHub App settings:

```
Private keys
  → Generate a private key
```

 GitHub will download a `.pem` file.

 For example:

```
pr-explain.private-key.pem
```

 Keep this file secure.

 GitHub documentation:

 Managing private keys for GitHub Apps

---

 # GitHub App Environment Variables

 Configure:

```
GITHUB_APP_ID=123456

GITHUB_APP_PRIVATE_KEY_FILE=/absolute/path/to/pr-explain.private-key.pem

GITHUB_WEBHOOK_SECRET=your-webhook-secret
```

 When using Docker Compose, the private key is mounted into the containers as:

```
/run/secrets/github-app.pem
```

 The application reads the private key from the mounted file.

 Keep this empty when using the file-based configuration:

```
GITHUB_APP_PRIVATE_KEY=
```

---

 # Install the GitHub App

 After creating the GitHub App:

 1. Open the GitHub App settings.
2. Select **Install App**.
3. Select the GitHub account or organization.
4. Choose the repositories where PR Explain should operate.
5. Complete the installation.

 For security, use **Only select repositories** if PR Explain should only operate on specific repositories.

---

 # Local Development

 ## Prerequisites

 Install:

 - Docker Desktop
- Git
- GitHub account
- GitHub App
- Cloudflare Tunnel or another webhook tunnel

 Docker Compose runs the local application stack.

 The local stack contains:

```
PostgreSQL
Ollama
Ollama model downloader
FastAPI API
Background worker
React frontend
```

---

 # Clone the Repository

```
git clone https://github.com/rajatrao/PR_explain.git

cd PR_explain
```

---

 # Configure Environment

 Create your environment file:

```
cp .env.example .env
```

 At minimum, configure:

```
GITHUB_APP_ID=YOUR_APP_ID

GITHUB_APP_PRIVATE_KEY_FILE=/absolute/path/to/pr-explain.private-key.pem

GITHUB_WEBHOOK_SECRET=YOUR_WEBHOOK_SECRET
```

---

 # Local AI with Ollama

 Local development uses Ollama running in Docker.

 The default provider is:

```
LLM_PROVIDER=ollama
```

 The default configuration is:

```
LLM_PROVIDER=ollama

OLLAMA_BASE_URL=http://127.0.0.1:11434

OLLAMA_MODEL=qwen3-coder:30b

OLLAMA_TIMEOUT_MS=180000
```

 The first Docker Compose startup downloads the configured model into a persistent Ollama volume.

 This allows the entire AI workflow to run locally without sending the explanation packet to a cloud AI provider.

---

 # Start the Application

 Run:

```
docker compose up
```

 The local services are:

 | Service | Address |
| --- | --- |
| React frontend | `http://localhost:5173` |
| FastAPI API | `http://localhost:8000` |
| Ollama | `http://localhost:11434` |
| PostgreSQL | `localhost:5432` |

The first startup may take some time because the Ollama model needs to be downloaded.

---

 # Expose the API to GitHub

 Start a local webhook tunnel:

```
cloudflared tunnel --url http://localhost:8000
```

 Then configure the GitHub App webhook:

```
https://<tunnel-host>/api/webhooks/github
```

 For example:

```
https://example.trycloudflare.com/api/webhooks/github
```

 Again:

 **Do not expose port `11434` publicly.**

 The webhook tunnel should only point to the FastAPI API.

---

 # Local Architecture

 Mermaid flowchart: GitHub, Cloudflare Tunnel, FastAPI API, ("PostgreSQL"), Background Worker, Ollama Docker, qwen3-coder:30b, React UI

The important architectural property is:

```
GitHub
   │
   ▼
FastAPI
   │
   ▼
Worker
   │
   ▼
Ollama
```

 The FastAPI API does **not** directly perform model inference.

 The worker owns the long-running analysis and AI interaction.

 This keeps webhook processing fast and prevents model inference from blocking HTTP requests.

---

 # Production AI

 Production deployments can use a public AI model such as OpenAI instead of running Ollama.

 The provider is selected through configuration.

 ## Local

```
LLM_PROVIDER=ollama
```

 Flow:

```
Worker
   │
   ▼
Ollama
   │
   ▼
Local AI Model
```

 ## Production

```
LLM_PROVIDER=openai
```

 Flow:

```
Worker
   │
   ▼
OpenAI API
   │
   ▼
Cloud AI Model
```

 No application code changes are required to switch between these configurations.

---

 # OpenAI Configuration

 For production, configure:

```
LLM_PROVIDER=openai

LLM_BASE_URL=https://api.openai.com/v1

LLM_API_KEY=your-openai-api-key

LLM_MODEL=your-model-name
```

 For example:

```
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=...
LLM_MODEL=...
```

 OpenAI API documentation:

 OpenAI API Quickstart

 Store the API key using your deployment platform's secret manager.

 Do not commit:

```
LLM_API_KEY=...
```

 to Git.

---

 # Local vs Production AI

 |  | Local Development | Production |
| --- | --- | --- |
| Provider | Ollama | OpenAI |
| Runtime | Docker | Cloud API |
| Model | `qwen3-coder:30b` | Configured cloud model |
| Network | Local | Internet |
| API key | Not required | Required |
| Ollama | Required | Not required |
| Application code | Same | Same |
| Configuration | `LLM_PROVIDER=ollama` | `LLM_PROVIDER=openai` |

The AI provider is an explicit configuration choice.

 There is no automatic fallback between providers.

 For example:

```
LLM_PROVIDER=openai
```

 does not silently fall back to Ollama.

 Likewise:

```
LLM_PROVIDER=ollama
```

 does not automatically send data to a public AI provider.

 This makes the data-flow and privacy decision explicit.

---

 # Production Architecture

 A typical production deployment looks like:

 Mermaid flowchart: GitHub, GitHub App, HTTPS / Load Balancer, FastAPI API, ("PostgreSQL"), Background Worker, OpenAI API, React Web App, Pull Request Comment

The production deployment can therefore be thought of as:

```
                        GitHub
                           │
                           │ Webhook
                           ▼
                  ┌─────────────────┐
                  │   FastAPI API   │
                  │                 │
                  │ Webhooks / API  │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │   PostgreSQL    │
                  │                 │
                  │ Jobs / Analysis │
                  │ Claims / Output │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ Background      │
                  │ Worker          │
                  │                 │
                  │ Analysis + AI   │
                  └────────┬────────┘
                           │
                    Explanation
                       Packet
                           │
                           ▼
                  ┌─────────────────┐
                  │    OpenAI API   │
                  │                 │
                  │   AI Model      │
                  └─────────────────┘
```

---

 # Data Flow and Trust Boundary

 One of the most important properties of PR Explain is making the AI data path explicit.

 ## Local AI mode

```
GitHub
   │
   ▼
Self-hosted API
   │
   ▼
Self-hosted Worker
   │
   ├── Repository snapshot
   ├── Pull Request diff
   ├── Symbols
   ├── Evidence
   └── Explanation packet
             │
             ▼
       Local Ollama
```

 In this mode, AI inference is performed locally on infrastructure you control.

---

 ## Cloud AI mode

```
GitHub
   │
   ▼
Self-hosted API
   │
   ▼
Self-hosted Worker
   │
   └── Explanation packet
             │
             ▼
        OpenAI API
```

 When:

```
LLM_PROVIDER=openai
```

 the configured explanation packet is sent to the cloud AI provider.

 This is an explicit configuration decision.

---

 # Why the Worker Exists

 AI inference and repository analysis can take significantly longer than a normal HTTP request.

 The webhook path therefore does not perform the complete analysis synchronously.

 Instead:

```
GitHub
   │
   ▼
Webhook
   │
   ▼
FastAPI
   │
   ▼
Create Job
   │
   ▼
Return
```

 The worker then performs:

```
Job
 │
 ├── Repository snapshot
 ├── Diff analysis
 ├── Symbol analysis
 ├── Change graph
 ├── Evidence
 ├── Impact
 ├── Explanation packet
 ├── AI inference
 └── GitHub comment
```

 This provides:

 - Fast webhook responses
- Retryable background jobs
- Better failure isolation
- Long-running AI inference outside the HTTP request
- Separation between API and processing workloads

---

 # Analysis Pipeline

 The current processing pipeline is:

```
snapshot_fetch
      │
      ▼
diff_analysis
      │
      ▼
symbol_analysis
      │
      ▼
change_graph
      │
      ▼
evidence
      │
      ▼
impact
      │
      ▼
claims_persisted
      │
      ▼
explanation_packet_persisted
      │
      ▼
explanation
      │
      ▼
comment
```

---

 # Deterministic Analysis

 The deterministic analysis stage produces facts about the Pull Request.

 Examples include:

 ### Files

```
src/payment/service.py
src/payment/repository.py
tests/payment/test_service.py
```

 ### Symbols

```
PaymentService.process_payment
PaymentRepository.create
PaymentEventPublisher.publish
```

 ### Relationships

```
PaymentService.process_payment
        │
        ├── calls → PaymentRepository.create
        │
        └── calls → PaymentEventPublisher.publish
```

 ### Evidence

 The analysis can associate claims with the relevant source or diff evidence.

 ### Impact

 Files and symbols outside the direct diff can be identified when they are relevant to the change.

---

 # Explanation Packet

 The AI model does not receive an unrestricted repository and is not expected to independently discover the entire change.

 Instead, the worker constructs a bounded explanation packet.

 Conceptually:

```
Explanation Packet
├── Pull Request metadata
├── Changed files
├── Changed symbols
├── Relationships
├── Evidence
├── Impact
├── Claims
└── Relevant context
```

 The packet is intentionally bounded to prevent unnecessarily large model requests.

---

 # AI Explanation

 The AI model takes the explanation packet and turns it into human-readable language.

 Conceptually:

```
Change Graph
     +
Evidence
     +
Impact
     +
Claims
     │
     ▼
Explanation Packet
     │
     ▼
AI Model
     │
     ▼
Human-readable Explanation
```

 The model should explain the supplied facts rather than invent unsupported relationships.

---

 # Failure Handling

 PR Explain separates:

```
analysis_status
explanation_status
comment_status
```

 This is important because deterministic analysis and AI narration are independent stages.

 For example:

```
GitHub PR
    │
    ▼
Analysis
    │
    ▼
Claims Stored
    │
    ▼
AI Explanation
    │
    ├── SUCCESS
    │
    └── FAILURE
```

 If the AI provider fails:

 - Deterministic analysis is not discarded.
- Claims remain stored.
- Files and symbols remain available.
- Explanation can be retried.
- The Pull Request comment is not posted until explanation succeeds.

 Similarly, if the GitHub comment update fails, the stored analysis and explanation remain available in the application.

---

 # Pull Request Comment Behavior

 PR Explain maintains one conversation-style explanation comment per Pull Request.

 When a Pull Request receives a new commit:

```
New Commit
    │
    ▼
New HEAD SHA
    │
    ▼
New Analysis
    │
    ▼
New Explanation
    │
    ▼
Existing PR Comment Updated
```

 This avoids creating a new comment for every commit.

 The application can also compare claim sets across different Pull Request head SHAs to expose revision deltas.

---

 # Web Application

 The React frontend provides two primary views.

 ## Explain

 The Explain view provides the high-level change story and diagram.

 It answers:

 > What changed and how does this change affect the system?

 ## Details

 The Details view exposes the underlying evidence:

 - Changed files
- Symbols
- Call relationships
- Line-level evidence
- Tests
- Relevant files outside the diff
- API facts
- Unknown facts
- Analysis status
- Explanation status

 Both views are backed by the same stored analysis.

---

 # Project Structure

```
PR_explain/
├── backend/
│   └── app/
│       ├── api/
│       ├── analysis/
│       ├── llm/
│       ├── worker/
│       └── ...
│
├── frontend/
│   └── ...
│
├── fixtures/
│   └── bench/
│
├── .env.example
├── docker-compose.yml
├── README.md
└── .gitignore
```

 Main runtime components:

 | Component | Responsibility |
| --- | --- |
| `backend` | API, GitHub integration, analysis and worker |
| `frontend` | React web application |
| `postgres` | Persistent application data |
| `worker` | Background analysis and AI explanation |
| `ollama` | Local AI inference |
| `ollama-pull` | Downloads the configured Ollama model |
| `fixtures` | Test and benchmark fixtures |

---

 # Environment Variables

 ## GitHub

```
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY_FILE=
GITHUB_WEBHOOK_SECRET=
GITHUB_API_URL=https://api.github.com
```

 ## Local Ollama

```
LLM_PROVIDER=ollama

OLLAMA_BASE_URL=http://127.0.0.1:11434

OLLAMA_MODEL=qwen3-coder:30b

OLLAMA_TIMEOUT_MS=180000
```

 ## Cloud AI

```
LLM_PROVIDER=openai

LLM_BASE_URL=https://api.openai.com/v1

LLM_API_KEY=

LLM_MODEL=
```

 ## Database

```
DATABASE_URL=postgresql+psycopg://pr_explain:pr_explain@localhost:5432/pr_explain

POSTGRES_USER=pr_explain
POSTGRES_PASSWORD=pr_explain
POSTGRES_DB=pr_explain
```

 ## Application

```
APP_BASE_URL=http://localhost:5173

CORS_ORIGINS=http://localhost:5173
```

 ## Analysis limits

```
EXPLANATION_PACKET_CHAR_BUDGET=32000

FANOUT_CAP=50

MAX_CHANGED_SYMBOLS=80
```

 Use `.env.example` as the source of truth for the current supported configuration.

---

 # Running Tests

 The test suite can run without Ollama or GitHub.

```
cd backend

python3 -m venv .venv

source .venv/bin/activate

pip install -e ".[dev]"

pytest
```

 Tests cover areas including:

 - OAuth fixture claims
- Citation validation
- Worker failure behavior
- Comment rendering

 The test suite does not need to download an AI model or contact GitHub.

---

 # Running the UI Without Docker Compose

 For lightweight local development:

```
cd backend

DATABASE_URL=sqlite:///./dev.db \
PYTHONPATH=. \
python -m app.seed
```

 Start FastAPI:

```
DATABASE_URL=sqlite:///./dev.db \
PYTHONPATH=. \
uvicorn app.api:app --port 8000
```

 In another terminal:

```
cd frontend

npm install

npm run dev
```

 Then open the URL printed by the frontend development server.

---

 # Model Benchmark

 PR Explain includes a model benchmark for testing different AI providers and models against frozen explanation packets.

 Run:

```
python -m app.llm.bench
```

 The benchmark produces a local JSON report covering areas such as:

 - Grounding
- Structure
- Latency
- Process memory

 The benchmark is intended to evaluate the model/provider configuration.

 It is **not** a Pull Request quality score.

---

 # Security

 ## Never commit secrets

 Do not commit:

```
.env
*.pem
GitHub App private keys
GitHub webhook secrets
LLM API keys
Database passwords
```

---

 ## Protect the GitHub App private key

 The GitHub App private key grants the application authentication capabilities as the GitHub App.

 Keep it outside source control and inject it into the application using a secure file or secret-management mechanism.

---

 ## Keep Ollama private

 For local development, Ollama is bound to:

```
127.0.0.1:11434
```

 Do not expose Ollama through your GitHub webhook tunnel.

 Only the FastAPI API needs to be publicly reachable:

```
Internet
    │
    ▼
FastAPI :8000
```

 not:

```
Internet
    │
    ▼
Ollama :11434
```

---

 # Production Secrets

 Use your deployment platform's secret manager for:

```
GITHUB_APP_PRIVATE_KEY
GITHUB_WEBHOOK_SECRET
LLM_API_KEY
POSTGRES_PASSWORD
```

 Do not store production secrets in the Git repository.

---

 # Production Deployment Checklist

 Before deploying PR Explain:

 - [ ] Create the GitHub App
- [ ] Configure Metadata → Read
- [ ] Configure Contents → Read
- [ ] Configure Pull requests → Write
- [ ] Enable installation events
- [ ] Enable installation repository events
- [ ] Enable Pull Request events
- [ ] Generate GitHub App private key
- [ ] Store the private key securely
- [ ] Configure `GITHUB_APP_ID`
- [ ] Configure `GITHUB_WEBHOOK_SECRET`
- [ ] Install the GitHub App on the required repositories
- [ ] Deploy FastAPI with HTTPS
- [ ] Configure the GitHub webhook URL
- [ ] Deploy PostgreSQL
- [ ] Deploy the background worker
- [ ] Configure `LLM_PROVIDER=openai`
- [ ] Configure `LLM_BASE_URL`
- [ ] Configure `LLM_API_KEY`
- [ ] Configure `LLM_MODEL`
- [ ] Configure frontend production API origin
- [ ] Configure CORS
- [ ] Configure persistent database storage
- [ ] Configure application logging and monitoring
- [ ] Test a Pull Request from webhook through analysis to GitHub comment
- [ ] Verify that Ollama is not publicly exposed
- [ ] Verify that secrets are not present in logs

 For production, use a real HTTPS server/application endpoint rather than a development webhook tunnel.

---

 # Example: End-to-End Pull Request

 Suppose a developer opens:

```
PR #42

Add payment event publishing
```

 The Pull Request modifies:

```
src/payment/service.py
src/payment/events.py
tests/payment/test_service.py
```

 PR Explain receives:

```
GitHub Webhook
      │
      ▼
FastAPI
      │
      ▼
Background Worker
```

 The worker analyzes the repository:

```
PaymentService.process_payment()
          │
          ├── PaymentRepository.create()
          │
          └── PaymentEventPublisher.publish()
```

 It collects:

```
Files
Symbols
Relationships
Evidence
Impact
Claims
```

 It creates:

```
Explanation Packet
```

 The configured AI provider then receives the packet.

 ### Local

```
Explanation Packet
       │
       ▼
Ollama
       │
       ▼
Local Model
```

 ### Production

```
Explanation Packet
       │
       ▼
OpenAI
       │
       ▼
Cloud Model
```

 The generated explanation is stored and posted to GitHub:

```
Worker
   │
   ├── PostgreSQL
   │
   └── GitHub API
          │
          ▼
     PR #42 Comment
```

---

 # Design Principles

 ## 1\. `EXPLAIN` before explanation

 Like SQL `EXPLAIN`, PR Explain first produces a structured representation of what is happening.

 The AI then explains that representation.

---

 ## 2\. Evidence before prose

 The system determines what changed before generating a natural-language explanation.

---

 ## 3\. AI is not the source of truth

 The model is a narrator over deterministic analysis.

---

 ## 4\. Local-first development

 Developers can run the complete AI workflow locally using Docker and Ollama.

 No cloud AI API is required for local inference.

---

 ## 5\. Production provider flexibility

 Production can use OpenAI or another compatible provider through configuration without changing the core analysis pipeline.

---

 ## 6\. Explicit data boundaries

 Switching from:

```
LLM_PROVIDER=ollama
```

 to:

```
LLM_PROVIDER=openai
```

 is an explicit decision to move AI inference from local infrastructure to a cloud provider.

---

 ## 7\. Failure isolation

 Analysis, explanation, and GitHub commenting are separate stages.

 A model failure should not destroy deterministic analysis.

---

 ## 8\. Minimal GitHub permissions

 The GitHub App requests only the repository permissions required by PR Explain.

---

 # Mental Model

 If you remember only one thing about PR Explain, remember this:

```
                 SQL
                  │
                  ▼
          ┌───────────────┐
          │    EXPLAIN    │
          └───────┬───────┘
                  │
                  ▼
          Execution Plan
                  │
                  ▼
         Human Understanding

                 Code
                  │
                  ▼
          ┌───────────────┐
          │  PR EXPLAIN   │
          └───────┬───────┘
                  │
                  ▼
       Change Graph + Evidence
                  │
                  ▼
             AI Model
                  │
                  ▼
         Human Understanding
```

 **PR Explain is `EXPLAIN` for Pull Requests.**

 It doesn't just ask an AI:

 > "What does this PR do?"

 It first asks the codebase:

 > "What actually changed, what does it connect to, and what evidence supports that?"

 Then it asks the AI:

 > "Now explain those facts to a human."

---

 # License

MIT — for hackathon / research use.
