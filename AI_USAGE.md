# How AI tools were used

Built end-to-end with **Claude Code** (Claude Sonnet 5.5) as pair-programmer, in a plan → build → verify loop.

| Phase | AI usage |
|---|---|
| Requirements | Fed the challenge + scoring-criteria slides to Claude; it produced `PLAN.md` mapping every criterion to a deliverable, ordered by priority |
| Architecture | Claude proposed ports/adapters so the app runs against a Mock Woo store *and* a real store; I accepted the Adapter/Strategy/Observer split |
| Implementation | Claude generated modules (sync, workflow engine, agent, API, UI) in dependency order, one layer at a time |
| Testing | Claude wrote the unit + e2e suite alongside the code; a failing test was diagnosed and fixed (test bug: sort order) |
| Verification | Server run in the in-app browser; UI flow exercised via script (simulate order → webhook → workflow → approve → agent); seed data re-tuned after noticing an unbalanced segment distribution |
| Product feature | The **agent itself** is Claude tool-use (`app/agent/agent.py`), with a deterministic fallback and a scripted fake LLM in tests |
| Docs | README, architecture notes and the video script were drafted by Claude and reviewed by me |

Human decisions: choosing the CRM topic, the human-approval rule for agent emails, segment thresholds, and what to cut from scope.
`CLAUDE.md` gives future AI sessions the repo conventions and commands.
