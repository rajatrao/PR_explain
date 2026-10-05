Write at most four sentences in the summary: what changed, what it calls, what is reached from outside the diff, and the tests. Leave the other arrays empty. Do not list every symbol. Do not use hop jargon. The change-flow story is drawn separately from the stored graph.

Also fill behavioral_changes, using BEHAVIOR FACTS only. Write it as the lead reviewer of this pull request: describe how the system behaves differently, as observable behavior and functional outcomes.
- Each change compares base and head: "Previously, <situation> was handled as <old outcome>. With this change, it is handled as <new outcome>." Put the first half in before and the second in after. Read the outcome from the before/after statements and their before_when/after_when conditions.
- Group facts that serve one capability or flow into one change. At most six changes, most consequential first. title names the capability in plain words, not a function name.
- impact says who notices: the entry points in reached_from and the callers in callers_at_head. Use notes for contract checks.
- Do not list files, functions, methods, or classes that changed. Do not write "added X" or "modified Y". Do not restate code lines.
- Name a function only if it appears in the facts you cite and its fact has public=true. Describe private helpers by what they do and name the public entry points that reach them.
- Do not guess intent, performance, security, or correctness. If a fact does not show an outcome, leave it out.
- fact_ids lists the change ids (b1, b2, …) each change or watch item is based on.
- overview is at most two sentences on the overall behavioral shift. watch holds up to three questions a reviewer should check, each citing fact_ids.
- If BEHAVIOR FACTS is empty, set behavioral_changes to null.
