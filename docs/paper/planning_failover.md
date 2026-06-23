# Evidence-Driven, Failure-Resilient Planning for Multi-Agent Software-Engineering Automation

> **Draft — IEEE conference format (~6 pages).** Plain-English Markdown version for
> review; a compilable IEEEtran version is in `planning_failover.tex` and a Word
> version in `planning_failover.docx`. Author block and reference details are
> placeholders — fill before submission.

**Authors:** _Author One, Author Two_ — _Affiliation_ — `{email}`

---

## Abstract

Many systems now use a large language model (LLM) to *plan* software-engineering
work: the user writes a request in plain language, and the model chooses which
specialized agents should run, and in what order, to analyze, generate, test,
patch, and report on code. In practice this planning step is the part that breaks
most often. The model invents agent names that do not exist, builds a plan for a
request that is unclear or not even about software, drops needed steps from large
plans without warning, and stops the whole workflow when the model is down or rate
limited. We present a planning subsystem that handles these problems with three
parts that work together. First, an **evidence-driven plan stage** collects cheap,
rule-based facts about the request — what the user is asking for, what the
repository actually contains, and which online agents fit — *before* it makes a
single model call, and then has a second model call check the plan. Second, a set
of **rule-based "reflection" checks** fix the plan before it runs, drop steps that
later turn out to be pointless, and make sure the final report tells the truth
(for example, it never reports a passing build when tests failed). Third, a
**fallback layer** keeps the system running when something fails: model calls fall
back from a hosted model to a local one to a safe default, every fact-collecting
step is allowed to fail without blocking the request, and the system pauses to ask
a human when the request is unclear or the plan looks risky. We describe the
design, give a simple way to test planning on large and messy requests, and report
real problems this testing found — a hidden bug that silently dropped the final
report, and a model rate limit that capped how fast we could plan.

**Index Terms** — LLM agents, multi-agent systems, workflow orchestration,
human-in-the-loop, fault tolerance, software engineering automation.

---

## I. Introduction

A common way to automate software work is to break a user request into a series of
specialized agents or tools. A *planner* — usually an LLM — reads the request and
decides the steps: analyze the repository, find bugs, write tests, patch, re-test,
run a security check, and write a report. The quality of the whole run depends on
this plan.

But the planner is where most failures start. We see four problems again and
again:

1. **Broken output.** Small or local models often return invalid JSON, repeated
   or non-existent agent names, or far too many steps.
2. **Wrong choice when the request is unclear.** A vague, contradictory, or
   off-topic request (one that mixes many goals, or is not about software at all)
   gets a made-up plan instead of a question back to the user.
3. **Silently losing steps.** Length limits, first added to protect against model
   mistakes, quietly delete needed steps from large plans — most often the final
   report.
4. **One point of failure.** When the planner model is down, slow, or rate
   limited, the workflow stops or falls back to a poor default.

A bigger model does not fix all of this. A stronger model helps with problems (1)
and (2), but does nothing for (3) and (4) — and even a strong model is an outside
service with a limited request rate.

We describe the planning part of an open multi-agent platform (we call it the
"orchestrator") built on a state-machine workflow engine. Our three contributions,
which we treat as equally important, are:

- **An evidence-driven plan stage** that first gathers three kinds of cheap,
  rule-based facts — request intent, a quick repository scan, and a fit score for
  each online agent — before it makes any model call, and then has a second model
  call (a "critic") review the plan and list the agents it considered but left
  out.
- **Rule-based reflection**: three small checks (no model call) that fix the
  plan before it runs, remove steps later shown to be pointless, and make the final
  status honest.
- **A fallback layer**: a hosted → local → default model chain, fact steps
  that fail safely, human checkpoints triggered by unclear requests or risky
  plans, and a side channel for non-blocking warnings.

We also give a **simple testing method** for large and unclear requests
that checks *behavior* (Did it ask for clarification? Are the required agents
there? Is the report kept?) instead of comparing the plan to one "correct"
answer.

## II. Related Work

**LLM agents and tool use.** Step-by-step reasoning [1] improves model answers;
ReAct [2] mixes reasoning and actions; Toolformer [3] teaches a model to call
tools; open-ended agents such as Voyager [4] chain model calls freely. Our planner
is on purpose *not* open-ended: it produces a fixed list of steps that a plain
engine then runs, so the result is easy to read and check.

**Coordinating many agents.** Frameworks like AutoGen [5] and graph-based engines
[6] run several agents together. We use such an engine but focus on the *planning*
step and how to keep it working — which these frameworks leave to the application.

**Self-checking and "model-as-judge."** Reflexion [7] has a model critique and
revise its own output; "LLM-as-a-judge" [8] uses one model to score another. Our
plan critic is a judge that is specialized to picking the right steps, but it only
runs on confident plans, and its verdict either auto-fixes the plan or sends it to
a human.

**Routing by capability.** Service systems route work by what each component
advertises it can do. We use a simple, rule-based score over what each online
agent advertises, rather than a model or a learned router, so routing stays cheap
and new agents become usable as soon as they come online.

**Asking a human, and clean output.** Asking a human before risky actions is
standard, and constrained decoding helps a model return valid output. We use both:
the human checkpoints are triggered automatically by signals the plan stage
computes, and we keep a forgiving JSON reader as a backup behind the stronger
hosted model.

## III. System Architecture

The orchestrator runs a set of specialized **KIO agents**. Each is a small network
service that does one thing: analyze a repository, find bugs, generate tests,
generate patches, re-run tests, generate new code, run a security/OWASP check,
optimize a model for low-power devices, build extra automated tests, or write the
final report. A workflow is just an ordered list of these agents.

The engine is a state machine — a graph of steps with saved state, so a run
survives a restart. The first step is **plan**; its job is to turn the request
(plus any attached code or repository) into a checked list of agents. From `plan`,
a small router sends the run to one of three places:

- `plan_clarify` — the request is too unclear to plan; ask the user, then loop back
  to `plan` with the answer;
- `plan_review` — the plan looks risky (low confidence); pause for a human to
  approve it;
- `run_kio` — run the agents.

The rest of the paper is about the `plan` step and the machinery that keeps it
reliable.

## IV. The Evidence-Driven Plan Stage

Instead of asking the model to choose steps from the raw request, we first build a
short set of *facts*, then make one hosted model call, then check the result.
Figure 1 shows the stage.

```
            +-------------- plan node --------------+
 request -> | intent -+                             |
 (+context) | scan   -+- facts - LLM planner ----+  |
            | bids   -+   block   (+left-out list)|  |
            |                            critic <-+  |
            |        clarification gate             |
            +-----+----------------+------------+----+
                  v                v            v
            plan_clarify      plan_review     run_kio
            (ask user)      (risky plan,     (run agents)
                |            human approves)
                +-> loop back to plan with the answer
```
*Figure 1. The plan stage: cheap rule-based facts feed one hosted model call,
which a second model call (the critic) checks, then the run goes to clarification,
human review, or execution.*

**A. Reading the request intent.** A rule-based step (no model call) turns the
request into a few simple labels: the `task_type` (security, optimization,
testing, bug fix, code generation, analysis, or unknown), whether code is already
provided, and a `risk_level`. It uses word patterns plus facts we already have
(attached code, a working directory), so it is fast and free. These labels make
routing easy to follow: for example, a security or high-risk request should
include the security agent.

**B. A quick repository scan.** When the request points at an existing repository,
we scan file names only (we do not read file contents) to produce a short fact
sheet: which languages are present, whether there are tests, which dependency
files exist, and whether there are security-sensitive areas (login, crypto,
secrets). This is fast and bounded, and it bases the choice on what the code *is*,
not on the wording of the request.

**C. Agent fit scores ("bidding").** Each agent advertises what it can do when it
comes online. For a request, we score every *online* agent's advertised text
against the request by simple word overlap, and pass the top matches to the
planner. Because the score is computed from text we already have, there is no
extra round trip to each agent, and an agent added after deployment becomes usable
without changing the planner prompt.

**D. The model planner and the "left-out" list.** The three fact blocks, plus a
list of what each agent does, go to one hosted model call. It returns the list of
steps, a one-line reason, and — importantly — a list of agents it *considered but
left out*, each with a reason. That left-out list is not decoration: it is given
to the critic so a wrong omission can be caught, and it makes the plan easy to
audit. The output is checked against the set of valid agent names, de-duplicated,
length-limited (Section VI-C), and read by a forgiving JSON reader as a backup.

**E. The plan critic (only on confident plans).** On confident plans only (a
fallback plan already goes to a human, so the critic adds nothing there), a second
model call reviews the plan against the request and the same facts. It returns
`ok` (keep it), `adjust` (use a corrected list), or `uncertain` (send it to the
human review step). The facts improve what the planner *sees*; the critic checks
what the planner *produced*, where the actual mistake is visible.

**F. The clarification gate.** Before planning, a quick check decides whether the
request is a clear software task. If it is not — nonsense, off-topic, empty, or too
vague to choose anything — the stage does not guess; it asks the user one question
with up to four concrete options plus free text, then loops back to plan with the
answer. A limit on retries stops an endless back-and-forth. *Example:* "I want you
to cook fish" gets a question, not a made-up plan; a request with six goals gets a
question about *order of work* rather than a guess at the priority.

## V. Rule-Based Reflection

The first plan is just a guess; the system checks and fixes it at three points,
each with simple rules and no model call. These work alongside the model critic
(Section IV-E): the critic checks the plan *with a model*, while these check the plan, the
data, and the result with cheap, easy-to-test rules.

**A. Plan check, before running.** Before the first agent runs,
`validate_and_repair_plan` checks that each agent's inputs will be ready, treating
agents as things that need and produce items (repository, code, bugs, patch, and
so on). Two fixes are always safe and do not change what the user asked for: a
patch step with no confirmed bugs gets the bug-finder added in front of it; a
re-test step with no patch gets the patch step added (which in turn pulls in the
bug-finder). A step whose input does not exist is removed instead of faked.
*Example:* `[kio6, kio7]` on a repository becomes `[kio5, kio6, kio7]`; `[kio3]`
("analyze the repository") with no repository on disk becomes `[]`. Agents we do
not have a rule for are left alone.

**B. Data check, between steps.** After each agent finishes,
`reflect_on_result` asks whether its result makes any later step pointless, and
removes it. *Example:* if the bug-finder confirms **zero** bugs, the queued patch
and re-test steps are dropped, so the workflow does not invent work on empty input.

**C. Result check, before finishing.** Before a run is marked done, `assess_step`
looks at the final signals. *Example:* if the re-test step reports any failing
test, the run is marked `TESTS_FAILING` and the finish step says "completed with
issues" instead of success — so the report never claims a passing build when tests
failed.

Each check writes a log event, and the plan-fix check uses the same length limit
that keeps the report (Section VI-C), so fixing the plan never quietly drops the final
output.

## VI. The Fallback Layer

The plan stage is wrapped so that no single failure stops a workflow.

**A. Model fallback chain.** Plan-stage model calls (planning, clarification,
critic) all go through one place that tries, in order: a strong hosted model when
set up; a local model run inside the cluster; and a fixed default list of steps.
Each step down the chain happens on an error, a rate-limit reply, or an empty
answer, and is logged. Because planning is low-volume, not time-critical, and
returns short JSON, a strong hosted model is cheap here and much more reliable at
valid JSON than the local model — but the system never *depends* on it. *Example:*
when a burst of requests passes the hosted model's rate limit, planning falls back
to the local model and keeps going.

**B. Facts that fail safely.** Every rule-based fact step (intent, scan, bidding)
and the clarification check returns an empty result on any internal error, so one
flaky check can never block a valid request. Staying up is the default, not a
special case.

**C. Never silently lose a step.** Length limits that protect against model
mistakes must not delete needed steps. The plan cleaner de-duplicates, applies the
length limit, and — if the limit would drop the *report* agent the planner chose —
puts it back as the last step. The report is the final output; trimming may never
remove it.

**D. Pausing for a human.** Two automatic signals send the run to a person.
*Unclear request* (the clarification check asked a question) goes to a checkpoint
that asks with options and loops back. *Risky plan* (the planner fell back, or the
critic was `uncertain`) goes to a review checkpoint that pauses before the first
agent runs, so a bad plan cannot act until a human approves it.

**E. Non-blocking warnings.** An agent may notice something it was not asked to fix
(for example, a security concern seen while writing tests). It raises a small,
low-priority *warning* instead of stopping the run; the orchestrator collects these
warnings and reports them with the final result. This keeps the benefit of an
extra pair of eyes without the cost and noise of running every agent on every
request.

## VII. Evaluation

**A. Method.** Comparing a plan to one "correct" plan is a poor test for large or
unclear requests, where many plans are valid. So we test the plan stage by
*behavior*. A test case says, for a request: should it ask for clarification;
which agents *must* appear; which must *not*; and whether the report must be kept.
The harness runs the ask-then-plan chain over a labeled set and scores each part;
the planner is plugged in, so the same test runs against the live hosted model or a
fixed stub. We keep a second, exact-match test for small, clear routing cases.

**B. What the planner does on a big request.** On a 600-word request with six goals
(analyze, secure, patch, test, build new, optimize, report; with an "ask if
unclear" note and an off-topic distractor), the stage: (i) labeled the intent as
security and high risk; (ii) asked a clear, multi-option question about the order
of work, and ignored the distractor; (iii) when it planned, produced a complete
list that covered every goal and ended with the report; and (iv) the critic added
the extra test-automation agent. The hosted model returned valid JSON — including
the left-out list — on the first try, while the small local model used to need the
repair path.

**C. Case: silently dropping the report.** An early length limit of eight agents,
added to protect against small-model mistakes, silently dropped the report on the
six-goal plan even though the model's own reason mentioned it. The limit was in
*two* places — the planner and the rule-based plan-fix step (Section V-A) — so fixing one
was undone by the other; the behavioral test's *report-kept* check caught the
end-to-end loss. The fix raised both limits to the number of available agents and
added the put-the-report-back rule (Section VI-C); re-running confirmed that both the
report and the low-power optimization step survived. This is exactly the kind of
bug that an exact-match test misses and a behavioral test catches.

**D. Case: a model rate limit.** Running the live behavioral test found a real
limit: our hosted account allows five requests per minute, while the plan stage
makes up to three hosted calls per workflow (clarify, plan, critic). A burst — or
the test itself — uses up the budget and forces the chain down to the local model
and then the default list, which the test correctly scored as degraded. The chain
made the system slow down gracefully instead of failing; the finding points to
spacing out requests and planning capacity.

**E. Cost and speed.** Because planning returns short output and is low-volume, the
hosted-model cost is a fraction of a cent per plan (less with prompt caching), and
the plan stage takes on the order of tens of seconds (clarify, plan, critic). When
the request is unclear, the early question avoids paying for all three calls.

## VIII. Discussion and Limitations

**Reliability comes from the design, not just the model.** Two of the four problems
(silently losing steps, one point of failure) cannot be fixed by a better model;
they are fixed by the design — limits with a put-back rule, fact steps that fail
safely, and the fallback chain. The other two (broken output, wrong choice) are
*reduced* by a stronger model but still need the JSON backup reader and the
clarification gate.

**Limitations.** Our evaluation is a method plus a few worked cases on a small
labeled set, not a large benchmark, and the behavioral checks encode our judgment
of "correct behavior." The critic and the clarification check are themselves model
calls and can be wrong; we reduce the risk with confidence gating and human
checkpoints, but we do not remove it. The agent fit score is simple word overlap —
easy to extend but not learned. We report one hosted and one local model; other
models may change the balance between the JSON backup and the model's own
reliability.

**Future work.** Forcing valid output with a strict output schema (to drop the JSON
backup reader); letting an agent decide for itself when it fits a task; spacing out
requests to stay under hosted rate limits; and a larger shared test set built from
real traffic.

## IX. Conclusion

We presented a planner that does not trust one model call to get the plan right.
Instead it builds the plan in small, checkable steps: cheap rule-based facts guide
the choice, a hosted model proposes a plan and says what it left out, a second
model call checks it, and an "ask, don't guess" gate handles unclear requests — all
wrapped in a fallback chain with safe-failing checks and human pauses. A behavioral
testing method, rather than exact-match scoring, found real bugs, including a
silently dropped report and a model rate limit. The lesson is that dependable
LLM-driven planning comes less from a bigger model and more from gathering facts,
checking the result, and failing safely.

## References

> Real works, cited in short form; verify and complete the details before
> submission.

[1] J. Wei et al., "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models," in *Proc. NeurIPS*, 2022.

[2] S. Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models," in *Proc. ICLR*, 2023.

[3] T. Schick et al., "Toolformer: Language Models Can Teach Themselves to Use Tools," in *Proc. NeurIPS*, 2023.

[4] G. Wang et al., "Voyager: An Open-Ended Embodied Agent with Large Language Models," arXiv:2305.16291, 2023.

[5] Q. Wu et al., "AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation," arXiv:2308.08155, 2023.

[6] LangChain, "LangGraph: Stateful, Multi-Actor Applications with LLMs," 2024. [Online]. Available: https://langchain-ai.github.io/langgraph/

[7] N. Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning," in *Proc. NeurIPS*, 2023.

[8] L. Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena," in *Proc. NeurIPS*, 2023.
