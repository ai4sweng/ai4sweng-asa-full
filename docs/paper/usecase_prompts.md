# Use-Case Prompts — Evidence-Driven Planning Paper

English prompts used in the use cases of `planning_failover.tex`, copied verbatim
from the behavior spec (`apps/orchestrator/src/eval/behavior_spec.py`) and the
plan-stage eval (`apps/orchestrator/src/eval/plan_stage_eval.py`). Use these as a
test set. Each prompt lists the behavior the plan stage is expected to show.

---

## 1. Clarification gate (must ask, not guess)
Paper §IV-F. The gate fires on off-topic / vague / nonsense and loops back.

1. `I want you to cook fish`
   → expect clarification (off-topic)
2. `asdfgh qwerty zxcv`
   → expect clarification (gibberish)
3. `make it better`
   → expect clarification (too vague, no context)

## 2. Clear single-intent routing (must NOT clarify)
Paper §VI / exact-match routing.

4. `build me a tool that converts CSV to JSON`
   → codegen → kio9
5. `write a python function that reverses a string`
   → codegen → kio9
6. `check this code snippet for bugs`   (code provided)
   → bug detect → kio5
7. `build me a CLI that converts CSV to JSON`
   → must include kio9; must exclude kio3 (no repo analyzer); no clarification

## 3. Security / audit routing
Paper §IV-A (intent = security/high-risk), §V.

8. `find security bugs in this repository`   (repo present, security-sensitive)
   → must include kio3, kio5 (kio12 allowed, not forbidden); no clarification
9. `analyze this repository for OWASP security issues and give me an audit report`
   (repo present, security-sensitive)
   → must include kio3, kio12, kio8; report must be kept; no clarification

## 4. Intent buried in a wall of text
Paper §VI — distractor must be ignored, real intent recovered.

10. `Here is a long story about our team, our deadlines, and our quarterly goals.
    We have shipped a lot. Anyway, all I actually need right now is a function that
    validates IBAN numbers. Thanks for reading all of this.`
    → must include kio9; must exclude kio3; no clarification

## 5. THE LONG PROMPT — large multi-goal initiative
Paper §VI-B and §VI-C (the report-drop bug case). Six goals + human-approval gate.
The report (kio8) must survive the length cap.

11. `Analyze our existing FastAPI monolith for bugs and security problems, patch the
    high-severity ones after human approval, generate a pytest suite, build a brand-new
    usage-metering microservice from scratch, optimise a small edge ML model for energy,
    and produce an executive audit report at the end.`
    (repo present, code provided, security-sensitive)
    → must include kio3, kio5, kio12, kio4, kio9, kio8; report must be kept (survives cap)

---

### Agent legend
- kio3  — repository analyzer
- kio4  — test generation
- kio5  — bug finder
- kio6  — patch generation
- kio7  — re-run tests
- kio8  — final report (terminal deliverable, never dropped)
- kio9  — code generation
- kio12 — security / OWASP check
