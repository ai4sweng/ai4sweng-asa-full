# Use-Case Coverage — Evidence-Driven, Failure-Resilient Planning

This document walks through every case discussed in `planning_failover.tex`. For
each case it gives a concrete English **prompt**, the **expected behavior**, and a
plain-English explanation of **how the system covers it** and **where** in the code
that lives. Prompts marked *(spec)* are taken verbatim from the behavior spec
(`apps/orchestrator/src/eval/behavior_spec.py`) and plan-stage eval
(`apps/orchestrator/src/eval/plan_stage_eval.py`).

Agent legend: kio3 repo-analyzer · kio4 test-gen · kio5 bug-finder ·
kio6 patch-gen · kio7 re-test · kio8 final report · kio9 code-gen ·
kio12 security/OWASP check.

---

## Part 1 — The four failure modes (Paper §I)

These are the problems the whole design exists to prevent. Each one has a prompt
that *provokes* the failure and a mechanism that *absorbs* it.

### Case 1.1 — Broken planner output
**Prompt:** `build me a tool that converts CSV to JSON` *(spec)*
**Failure provoked:** a small/local model returns invalid JSON, repeated names, or
non-existent agent ids (e.g. `kio99`, `kio_security`).
**How it is covered:** the plan cleaner validates every returned id against the
known agent set, de-duplicates, and a forgiving JSON reader recovers malformed
output; on confident-but-broken output the hosted→local→default chain still yields
a usable plan. Expected result: `[kio9]`, no clarification.
**Where:** plan sanitize + `LmEngineClient`, exercised by the `ROUTING_CASES`.

### Case 1.2 — Wrong choice on an unclear request
**Prompt:** `I want you to cook fish` *(spec)*
**Failure provoked:** the planner invents a software plan for an off-topic request.
**How it is covered:** the clarification gate classifies the request as not-a-clear-
software-task and asks the user a question instead of guessing (Paper §IV-F).
Expected result: clarification fires; no plan is produced yet.
**Where:** `assess_clarification`, scored by `CLARIFY_CASES`.

### Case 1.3 — Silently losing steps
**Prompt:** the long multi-goal prompt (Case 5.1 below).
**Failure provoked:** a length cap quietly deletes needed steps — most often the
final report.
**How it is covered:** the cleaner applies the cap *and* re-inserts the report agent
(kio8) as the last step if the cap would have dropped it; both the planner and the
plan-fix step use the same raised limit (Paper §VI-C).
**Where:** length cap + report put-back; reflection case
"oversized plan must cap at 12 and keep kio8 last".

### Case 1.4 — One point of failure
**Prompt:** any prompt issued during a hosted-model outage or rate-limit burst.
**Failure provoked:** the planner model is down/slow/rate-limited and the workflow
stalls.
**How it is covered:** the model fallback chain steps hosted → local → fixed default
on any error, rate-limit reply, or empty answer, and logs each step (Paper §VI-A).
**Where:** the single fallback wrapper around all plan-stage model calls.

---

## Part 2 — The evidence-driven plan stage (Paper §IV)

### Case 2.1 — Reading request intent (§IV-A)
**Prompt:** `find security bugs in this repository` *(spec, repo + security-sensitive)*
**Expected:** intent labeled `task_type=security`, `risk_level=high`.
**How it is covered:** a rule-based, no-model step turns the wording plus known
facts into simple labels, so a security/high-risk request is steered to include the
security agent. Expected plan includes kio3, kio5 (kio12 allowed).
**Where:** `extract_intent` / `format_intent` (`services/prompt_intent.py`).

### Case 2.2 — Repository scan (§IV-B)
**Prompt:** `analyze this repository for OWASP security issues and give me an audit report` *(spec)*
**Expected:** facts derived from filenames (languages, presence of tests, dependency
files, security-sensitive areas) — not from the wording.
**How it is covered:** a filename-only scan (no file contents read) bases routing on
what the code *is*. Expected plan includes kio3, kio12, kio8; report kept.
**Where:** repo-recon fact step (`test_repo_recon.py` covers it).

### Case 2.3 — Agent fit scores / bidding (§IV-C)
**Prompt:** any request after a new agent comes online.
**Expected:** the newly online agent is scored against the request by word overlap
and becomes usable with no prompt change.
**How it is covered:** every *online* agent's advertised text is scored against the
request; top matches are passed to the planner. No extra round trip.
**Where:** capability bidder (`test_capability_bidder.py`).

### Case 2.4 — The planner and the "left-out" list (§IV-D)
**Prompt:** the long multi-goal prompt (Case 5.1).
**Expected:** a step list, a one-line reason, and a list of agents *considered but
left out*, each with a reason — so a wrong omission is auditable.
**How it is covered:** one hosted call returns all three; output is validated,
de-duped, length-capped, and read by the forgiving JSON backup.
**Where:** plan_workflow output contract; the left-out list feeds the critic.

### Case 2.5 — The plan critic, on confident plans only (§IV-E)
**Prompt:** the long multi-goal prompt (Case 5.1).
**Expected:** a second model call reviews the plan and returns `ok` / `adjust` /
`uncertain`; in the paper's run it *added the test-automation agent*.
**How it is covered:** the critic runs only on confident plans (a fallback plan goes
straight to a human); `uncertain` routes to human review.
**Where:** plan critic (`test_plan_critic.py`).

### Case 2.6 — The clarification gate, the two examples (§IV-F)
**Prompt A:** `I want you to cook fish` *(spec)* → asks a question, no made-up plan.
**Prompt B:** the six-goal prompt (Case 5.1) → asks about *order of work* rather than
guessing the priority.
**How it is covered:** a quick check decides whether the request is a clear software
task; if not (nonsense / off-topic / empty / too vague) it asks one question with up
to four options plus free text, then loops back. A retry limit stops endless
back-and-forth.
**Where:** `assess_clarification`; `CLARIFY_CASES` includes `asdfgh qwerty zxcv`
(gibberish) and `make it better` (too vague).

---

## Part 3 — Rule-based reflection (Paper §V)

All three checks are deterministic, no model call.

### Case 3.1 — Plan check before running (§V-A)
**Prompt:** `patch this bug` with a plan of `[kio6, kio7]` *(spec, code provided)*
**Expected:** `[kio6, kio7]` → `[kio5, kio6, kio7]` (bug-finder inserted ahead of
the patch; re-test pulls in patch which pulls in the bug-finder).
**Counter-prompt:** `analyze the repo` → `[kio3]` with **no repo on disk** → `[]`
(a step whose input cannot exist is removed, not faked).
**Where:** `validate_and_repair_plan` (`engine/process_reflection.py`);
reflection cases "patch this bug (kio5 skipped by planner)" and
"analyze the repo (no working directory)".

### Case 3.2 — Data check between steps (§V-B)
**Prompt:** any bug-fix request where the bug-finder confirms **zero** bugs.
**Expected:** the queued patch and re-test steps are dropped — no work invented on
empty input.
**How it is covered:** after each agent finishes, `reflect_on_result` asks whether
the result makes any later step pointless and removes it.
**Where:** `reflect_on_result` (`test_data_reflection.py`).

### Case 3.3 — Result check before finishing (§V-C)
**Prompt:** any request whose re-test step reports a failing test.
**Expected:** run marked `TESTS_FAILING`; finish step says "completed with issues",
never claims a passing build.
**How it is covered:** `assess_step` inspects the final signals before the run is
marked done.
**Where:** `assess_step` (`test_process_reflection.py`).

---

## Part 4 — The fallback layer (Paper §VI)

### Case 4.1 — Model fallback chain (§VI-A)
**Prompt:** a burst of requests that passes the hosted model's rate limit.
**Expected:** planning falls back to the local model and keeps going (then to the
fixed default list if needed); each step logged.
**Where:** the single wrapper over plan/clarify/critic calls.

### Case 4.2 — Facts that fail safely (§VI-B)
**Prompt:** any valid request while one fact step (intent / scan / bidding) errors.
**Expected:** the failing fact step returns empty; the request is **not** blocked.
**How it is covered:** every rule-based fact step and the clarification check return
an empty result on internal error — staying up is the default.

### Case 4.3 — Never silently lose a step (§VI-C)
**Prompt:** the long multi-goal prompt (Case 5.1).
**Expected:** even when the cap trims the plan, the report agent the planner chose is
put back as the last step.
**Where:** reflection case "report requested but planner omitted kio8" and the
oversized-plan cap case.

### Case 4.4 — Pausing for a human (§VI-D)
**Prompt A (unclear):** `make it better` *(spec)* → clarification checkpoint with
options, loops back.
**Prompt B (risky):** the long multi-goal prompt when the planner fell back or the
critic returned `uncertain` → review checkpoint pauses before the first agent runs.
**Where:** clarify checkpoint + plan-review checkpoint (`test_hitl_approve.py`,
`test_confidence_gate.py`).

### Case 4.5 — Non-blocking warnings (§VI-E)
**Prompt:** `generate a pytest suite for this module` *(code provided)* where the
test-gen agent *notices* a security concern it was not asked to fix.
**Expected:** a low-priority warning is raised and reported with the final result;
the run is **not** stopped, and no extra agent is forced onto every request.
**Where:** advisory channel (`test_advisory_channel.py`).

---

## Part 5 — Evaluation case studies (Paper §VII)

### Case 5.1 — The big request (§VII-B) — THE LONG PROMPT
**Prompt:** *(spec)*
`Analyze our existing FastAPI monolith for bugs and security problems, patch the
high-severity ones after human approval, generate a pytest suite, build a brand-new
usage-metering microservice from scratch, optimise a small edge ML model for energy,
and produce an executive audit report at the end.`
**Expected behavior (the paper's run):** intent labeled security + high risk; the
gate asks a clear multi-option question about order of work and ignores the
distractor; the produced plan covers every goal and ends with the report; the critic
adds the test-automation agent; the hosted model returns valid JSON (incl. the
left-out list) on the first try.
**Coverage assertion:** must include kio3, kio5, kio12, kio4, kio9, kio8; report
must survive the cap. This single prompt exercises Cases 2.1, 2.4, 2.5, 2.6-B,
4.3 and 4.4-B at once.

### Case 5.2 — Silently dropping the report (§VII-C)
**Prompt:** the long multi-goal prompt (Case 5.1).
**Bug found:** an early cap of eight agents dropped the report even though the
model's own reason mentioned it; the limit lived in two places (planner *and* the
plan-fix step), so fixing one was undone by the other.
**How the test caught it:** the behavioral *report-kept* check flagged the end-to-end
loss — exactly the bug an exact-match test misses.
**Fix:** raise both limits to the agent count + the report put-back rule (Case 4.3).

### Case 5.3 — A model rate limit (§VII-D)
**Prompt:** a burst of the eval prompts run live (each makes ~2 model calls).
**Limit found:** the hosted account allows five requests per minute while the plan
stage makes up to three hosted calls per workflow (clarify, plan, critic); a burst
exhausts the budget and forces the chain down to local then default.
**How it is covered:** the eval pacing (`pace_seconds`) spaces calls; the chain
degrades gracefully and the test scores the run as degraded rather than failed.
**Where:** `pace_seconds` in `evaluate_plan_stage`; fallback chain (Case 4.1).

### Case 5.4 — Buried intent (extra behavioral case)
**Prompt:** *(spec)*
`Here is a long story about our team, our deadlines, and our quarterly goals. We have
shipped a lot. Anyway, all I actually need right now is a function that validates
IBAN numbers. Thanks for reading all of this.`
**Expected:** must include kio9; must exclude kio3; no clarification — the distractor
wall of text is ignored and the real codegen intent is recovered.

---

## Coverage matrix (case → mechanism → test)

| Case | Mechanism | Test home |
|------|-----------|-----------|
| 1.1 broken output | id-validate + JSON backup + chain | `ROUTING_CASES` |
| 1.2 unclear → ask | clarification gate | `CLARIFY_CASES` |
| 1.3 lost steps | cap + report put-back | reflection oversized-plan |
| 1.4 single point of failure | fallback chain | live rate-limit run |
| 2.1 intent | `extract_intent` | `test_prompt_intent.py` |
| 2.2 repo scan | filename recon | `test_repo_recon.py` |
| 2.3 bidding | capability bidder | `test_capability_bidder.py` |
| 2.4 left-out list | plan_workflow contract | `LONG_PROMPT_CASES` |
| 2.5 critic | confident-only critic | `test_plan_critic.py` |
| 2.6 clarify gate | `assess_clarification` | `CLARIFY_CASES` |
| 3.1 plan repair | `validate_and_repair_plan` | reflection cases |
| 3.2 data reflect | `reflect_on_result` | `test_data_reflection.py` |
| 3.3 result assess | `assess_step` | `test_process_reflection.py` |
| 4.1 model fallback | hosted→local→default | live rate-limit run |
| 4.2 facts fail safe | empty-on-error | fact-step tests |
| 4.3 keep the report | report put-back | reflection report case |
| 4.4 human pause | clarify + review checkpoints | `test_hitl_approve.py` |
| 4.5 advisory | non-blocking warning | `test_advisory_channel.py` |
| 5.1 big request | end-to-end plan stage | `LONG_PROMPT_CASES` |
| 5.2 report drop | behavioral report-kept | reflection oversized-plan |
| 5.3 rate limit | pacing + chain | `evaluate_plan_stage` |
| 5.4 buried intent | gate + routing | `LONG_PROMPT_CASES` |
