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
