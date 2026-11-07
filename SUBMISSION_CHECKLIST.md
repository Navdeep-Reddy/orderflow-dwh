# Submission checklist (YottaFlex AI guidelines)

| Guideline | Status |
|---|---|
| 01 Make it run — executable, clear steps | README "Quick start"; verified from a fresh copy (`make test/run/validate/report`) |
| 02 Public GitHub repo, evolution visible | **TODO (you):** create a *new, dedicated, public* repo, `git remote add origin …`, `git push`. Local history is a logical commit series (see below) |
| 03 LLM use transparent, prompt history | `docs/ai/PROMPT_HISTORY.md`, `docs/ai/AI_USAGE.md` — **complete the ✍ section and append any new prompts** |
| 04 Reasoning visible | `README.md`, `docs/DESIGN.md`, `docs/DATA_DICTIONARY.md`, `docs/WALKTHROUGH.md` |
| FAQ 3 | Send the repo link from the e-mail address used during recruitment, within 72 h of the send timestamp |
| FAQ 7 | Work must be individual: you must understand and own the code — use `docs/WALKTHROUGH.md` |
| FAQ 12 | No secrets / personal data: only synthetic data; verified no credentials in the tree |
| FAQ 11 | Limitations and deviations: README "Known limitations", `docs/DESIGN.md` |

**Notes on git history.** The commits were created in one session (real timestamps, not back-dated) and carry a
`Co-authored-by: Claude` trailer. Your 72-hour window is verified by artifact timestamps, so keep making your own
commits as you review, change and test things — that is the honest record of your contribution. Before pushing, set your identity:
`git config user.name "…" && git config user.email "…"` (existing commits use a placeholder author).
