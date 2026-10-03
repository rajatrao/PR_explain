# PR Explain

PR Explain turns one pull-request head SHA into evidence-backed claims, then asks a configured model to narrate those claims. Explain is the short change story, shown by default and posted as one pull-request comment. Details is the detailed page. If narration or the comment fails, the analysis stays.

The model does not analyze the repository. Deterministic analysis builds the change graph. The model only narrates a stored packet. Local Ollama is the default. The web app and the API never call the model.

## Run it

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Copy the environment file: `cp .env.example .env`
3. Start the stack: `docker compose up`

   The first start pulls `qwen3-coder:30b` (about 18GB) into the Ollama volume. On a Mac, Docker is a Linux VM and does not get Metal, so explanations run on CPU and can take minutes. That is expected. Changing `OLLAMA_BASE_URL` on the worker to another private Ollama is a config change, not a code change. To narrate with a cloud model instead of this local pull, see [Cloud deployment](#cloud-deployment).

4. For GitHub webhooks, tunnel only to FastAPI:

   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```

   Set the GitHub App webhook URL to `https://<tunnel-host>/api/webhooks/github`. Open the explanation page at [http://localhost:5173](http://localhost:5173).

5. Do not add port 11434 to the tunnel. Compose publishes Ollama on `127.0.0.1` only, on a network the API and the web app are not attached to.

On the GitHub App settings page, generate a private key. The browser downloads a `.pem` file.

Set `GITHUB_APP_PRIVATE_KEY_FILE` in the repo-root `.env` to the host path of that file. The variable is already in `.env.example`. In `docker-compose.yml`, the `api` and `worker` services mount that same host file read-only at `/run/secrets/github-app.pem` and set `GITHUB_APP_PRIVATE_KEY_FILE` inside those containers to `/run/secrets/github-app.pem`:

```yaml
- /path/to/app.private-key.pem:/run/secrets/github-app.pem:ro
```

The app reads the key from the mounted file. Leave `GITHUB_APP_PRIVATE_KEY` empty.

GitHub App permissions:

- Metadata: Read
- Contents: Read (required to download the repository tarball)
- Pull requests: Write (required to post and update the pull request comment)

Events: `installation`, `installation_repositories`, and `pull_request` (`opened`, `reopened`, `synchronize`, `closed`). `closed` does not analyze. There is no Checks permission and no review score.

## Cloud deployment

Ollama is the local default (`LLM_PROVIDER=ollama`). To narrate with a cloud model, or any host that accepts the same `/chat/completions` JSON, set these in the repo-root `.env`:

```bash
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=
LLM_MODEL=
```

`LLM_BASE_URL` is the API root, for example `https://api.openai.com/v1`. `LLM_MODEL` is that host's model name. `LLM_API_KEY` stays in `.env`. Do not commit `.env` or the key. `.env.example` has empty placeholders for these three variables.

The provider is an explicit choice. `LLM_PROVIDER=openai` with a missing base URL, API key, or model fails the explanation step. Cloud mode does not fall back to Ollama, and Ollama does not switch to a cloud model. An unknown `LLM_PROVIDER` fails the same way and leaves the analysis intact.

Cloud mode does not need the Ollama container for explanation. Postgres is still required. The webhook still needs a public URL, using the tunnel in [Run it](#run-it). The `api` and `worker` services still need `GITHUB_APP_PRIVATE_KEY_FILE` mounted, and the GitHub App permissions above stay the same. The model is used only in the explanation step, after analysis has stored the packet. The pull-request comment is posted only after that explanation succeeds.

`docker compose` still defines the `ollama` service so the local default can pull `qwen3-coder:30b`. When `LLM_PROVIDER=openai`, explanation does not call that container.

## What you get

- Analysis status and explanation status are stored separately. A failed explanation leaves claims, files, and symbols on the page.
- Two views share one analysis. Explain is the change story and the diagram. Details is what changed, who calls it, file and line, tests, why a file outside the diff matters, and API or unknown facts.
- One conversation comment per pull request, updated in place for each new head SHA. A failed GitHub write does not discard the page.
- Revision deltas compare claim sets across head SHAs.

## Analysis and explanation

Analysis builds deterministic facts from the head snapshot, the diff, and tree-sitter. There is no model in that path. The facts are stored as symbols, relationships, evidence, and claims.

Explanation narrates a bounded packet of those claims. Local Ollama is the default. `LLM_PROVIDER=openai` uses the configured cloud model for that narration. The model may not invent files, functions, or edges. If the model returns nothing usable, the document is filled from the packet claims.

`analysis_status`, `explanation_status`, and `comment_status` are stored separately. The GitHub comment is posted only after explanation succeeds. If explanation fails, the comment is skipped, and the stored analysis stays.

The stages run in this order:

`snapshot_fetch` → `diff_analysis` → `symbol_analysis` → `change_graph` → `evidence` → `impact` → `claims_persisted` → `explanation_packet_persisted` → `explanation` → `comment`

```mermaid
flowchart TD
  subgraph analysis [Analysis]
    snapshot_fetch["snapshot_fetch — fetch the head snapshot"]
    diff_analysis["diff_analysis — read the compare diff"]
    symbol_analysis["symbol_analysis — parse symbols with tree-sitter"]
    change_graph["change_graph — record calls and imports"]
    evidence["evidence — collect file and line evidence"]
    impact["impact — trace reach beyond the diff"]
    claims_persisted["claims_persisted — store symbols, relationships, evidence, and claims"]
    explanation_packet_persisted["explanation_packet_persisted — store the bounded packet"]
    snapshot_fetch --> diff_analysis
    diff_analysis --> symbol_analysis
    symbol_analysis --> change_graph
    change_graph --> evidence
    evidence --> impact
    impact --> claims_persisted
    claims_persisted --> explanation_packet_persisted
  end
  subgraph narration [Explanation and comment]
    explanation["explanation — narrate the packet with the configured model"]
    comment["comment — post the pull-request comment"]
    explanation --> comment
  end
  explanation_packet_persisted --> explanation
```

The LLM is used only in explanation, to narrate facts analysis already stored. The web UI reads those stored facts and the grounded document.

## Tests without Ollama or GitHub

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Pytest covers the oauth fixture claims, the citation validator, job failure with a fake provider, and the comment renderer. Nothing in that suite downloads a model or contacts GitHub.

To look at the page locally without Compose, from `backend/`:

```bash
DATABASE_URL=sqlite:///./dev.db PYTHONPATH=. python -m app.seed
DATABASE_URL=sqlite:///./dev.db PYTHONPATH=. uvicorn app.api:app --port 8000
```

In another shell, `cd frontend && npm install && npm run dev`, then open the runs the seed command printed.

## Model bench

`python -m app.llm.bench` sends the frozen packets in `fixtures/bench` through the configured provider and writes a local JSON report. The report scores grounding, structure, latency, and process memory. It is a model eval. It is not a pull-request quality score and it is not shown to repository users.

## Privacy

The default path keeps the snapshot, the packet, and the narration on infrastructure you run. `LLM_PROVIDER` defaults to `ollama`. That path stays on the machine, and a failure stays on that provider. Setting `LLM_PROVIDER=openai` sends the explanation packet to `LLM_BASE_URL`. That is an explicit choice. An unknown `LLM_PROVIDER` fails the explanation step and leaves the analysis intact.
