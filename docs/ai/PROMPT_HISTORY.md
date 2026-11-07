# Prompt history

Tool used: **Claude (Anthropic) in the claude.ai chat interface** — the only AI tool used for this project.
Per the FAQ, all prompts submitted are listed verbatim; responses are optional and summarised in `AI_USAGE.md`.

> **Limitation (disclosed):** the chat did not export a transcript file; the prompts below were copied from the conversation.
> Between prompts 1 and 2, the assistant ran code in its own sandbox (generating data, running tests/benchmark) —
> those were the assistant's tool calls, not prompts I typed.

## Prompt 1 — build request
Attachment: the project brief ( `prompt_01_attached_brief.txt` ).
```
Pls build this project
```

## Prompt 2 — requirements check
```
Is this fullfilling all the given description
```
(Result: the assistant listed coverage and said what it had not verified — clean-environment setup, Python-version
compatibility, pytest compatibility. I later verified the clean-environment flow; see `AI_USAGE.md`.)

## Prompt 3 — align with submission guidelines
Attachments: `YottaFlex_AI_Candidate_Project_Submission_Guidelines.pdf`, `YottaFlex_AI_Candidate_Project-FAQ.pdf`.
```
update accordingly
```

## Prompt 4+ (add here)
<!-- Append every further prompt you send to any AI tool, verbatim, before submitting. -->
