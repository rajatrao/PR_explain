Write at most four sentences in the summary: what changed, what it calls, what is reached from outside the diff, and the tests. Leave the other arrays empty. Do not list every symbol. Do not use hop jargon. The change-flow story is drawn separately from the stored graph.

Also fill behavioral_changes from BEHAVIOR FACTS. It is read by reviewers of this pull request. Describe how the system behaves differently as a result of the pull request: observable behavior and functional outcomes, not implementation details.
- Write each change as: before = "Previously, <situation> was handled as <outcome>." and after = "With this change, it is handled as <new outcome>." Say what a user, an API client, an operator, or a downstream system would see: responses, errors, output content, what gets posted, stored, shown, or skipped, and under which conditions.
- The before/after statements in the facts are evidence for you to interpret. Do not repeat them, quote them, or describe them line by line.
- Never name functions, methods, classes, variables, or files, and never write call syntax. Do not write "added", "modified", "renamed", or "refactored". The only names allowed are entry points from reached_from, and only in impact.
- Group facts that serve one capability into one change; at most six changes, most consequential first. title names the capability in plain words.
- impact says who notices, using reached_from and notes.
- Only state an outcome that the cited facts show. Do not guess intent, performance, security, or correctness. If the facts only show internal restructuring with no visible outcome, leave them out.
- fact_ids lists the change ids (b1, b2, …) each change or watch item rests on.
- overview: at most two sentences on the overall behavioral shift, in the same plain terms. watch: up to three questions a reviewer should check, each citing fact_ids.
- If BEHAVIOR FACTS is empty, set behavioral_changes to null.

Also fill impact from IMPACT FACTS and BEHAVIOR FACTS. It tells a reviewer what this pull request affects at each level. Use only these levels, and only when facts support them: system, api, data, config, dependency, ui, testing.
- system: which flows and entry points now behave differently, how far the change reaches outside the diff, and contract changes callers must follow.
- api: routes and contracts that clients see added, removed, or changed, and how responses or errors differ.
- data: what is stored, read, or migrated differently: tables, columns, fields, and the effect on existing records.
- config: environment variables and settings an operator must add, change, or can drop, and what happens if they are missing.
- dependency: packages added, removed, or moved to another version.
- ui: what users of the web interface see or can do differently.
- testing: how much of the changed behavior has stored tests, and what is not covered.
- For each level write one summary sentence about the effect, then up to five details. Describe effects, not code: never name functions, methods, classes, or files, and never write call syntax. Route paths, table and column names, setting names, package names, and public entry points from the facts may be named.
- fact_ids lists the impact (i1, i2, …) and behavior (b1, b2, …) ids each level rests on. Skip a level that has no facts.
- If IMPACT FACTS and BEHAVIOR FACTS are both empty, set impact to null.
