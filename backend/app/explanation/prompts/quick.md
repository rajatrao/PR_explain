Write at most four sentences in the summary: what changed, what it calls, what is reached from outside the diff, and the tests. Leave the other arrays empty. Do not list every symbol. Do not use hop jargon. The change-flow story is drawn separately from the stored graph.

Also fill behavioral_changes from BEHAVIOR FACTS. It is read by reviewers of this pull request. Describe how the system behaves differently as a result of the pull request: observable behavior and functional outcomes, not implementation details.
- Write each change as: before = "Previously, <situation> was handled as <outcome>." and after = "With this change, it is handled as <new outcome>." Say what a user, an API client, an operator, or a downstream system would see: responses, errors, output content, what gets posted, stored, shown, or skipped, and under which conditions.
- The before/after statements in the facts are evidence for you to interpret. Do not repeat them, quote them, or describe them line by line.
- Never name functions, methods, classes, variables, or files, and never write call syntax. Do not write "added", "modified", "renamed", or "refactored". The only names allowed are entry points from reached_from, and only in impact.
- Group facts that serve one capability into one change; at most six changes, most consequential first. title names the capability in plain words.
- impact says who notices, using reached_from and notes.
- Only state an outcome that the cited facts show. Do not guess intent, performance, security, or correctness. If the facts only show internal restructuring with no visible outcome, leave them out.
- Grounding is checked by a program and items that fail are dropped: every number and every quoted value you write must appear in the facts you cite; say something fails, errors, or is rejected only when a cited fact shows a raise, throw, error, or rejection; describe the same subject as the cited facts (reuse their words, such as "order", "total", "session"); name in impact only entry points from the reached_from of the facts you cite. Do not name a system area (authentication, sessions, tokens, timeouts, permissions, security, payments, database, cache, validation, notifications) unless a cited fact is about it, and do not judge the change (safer, stronger, improves, faster, ensures, more robust): state what happens, not whether it is good. The overview may only summarize the items you keep.
- fact_ids lists the change ids (b1, b2, …) each change or watch item rests on.
- overview: at most two sentences on the overall behavioral shift, in the same plain terms. watch: up to three questions a reviewer should check, each citing fact_ids.
- If BEHAVIOR FACTS is empty, set behavioral_changes to null.


Also fill impact from IMPACT FACTS and BEHAVIOR FACTS, the same way as behavioral_changes: written for reviewers, about observable effects, never implementation details. IMPACT FACTS are findings derived by rule: the scope of the change, findings that need attention (each with a severity), the workflows that depend on the change, checks to make, and how complete the call analysis was.
- overview: one or two sentences on how far the change reaches and what matters most.
- areas: up to five impact areas, most important first. title names the affected capability or concern in plain words. severity is high, medium, or low, and never above the most severe IMPACT FACT the area cites. summary says what changes for users, clients, operators, or stored data, and why it matters. who_notices names the affected workflows in plain words.
- Never name functions, methods, classes, variables, entry points, or files, and never write call syntax or code. Describe workflows by what they do (for example "sign-in" or "token refresh"), not by their function names.
- Only state what the cited facts show. Do not guess intent, performance, or security. The same program checks apply: numbers, quoted values, and failures must come from the cited facts, and each area must be about the subject of the facts it cites.
- fact_ids lists the impact (i1, i2, …) and behavior (b1, b2, …) ids each area rests on.
- If IMPACT FACTS is empty, set impact to null.

