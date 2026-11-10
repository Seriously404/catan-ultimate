## Long-Running Local Agent Reliability

- Work in small, independently verifiable steps.
- Before modifying a file, inspect whether the requested change has already been made.
- Prefer incremental edits over generating multiple large files in one response.
- After each completed step, verify the result before continuing.
- Maintain a brief progress record in `TASK_PROGRESS.md` for substantial tasks.
- Record completed work, remaining work, and any blockers.
- If an operation is retried, inspect the current filesystem state before repeating it.
- Avoid unnecessary full-codebase reads when relevant files have already been identified.
- Do not delegate to parallel subagents unless explicitly requested.
- Keep tool calls focused and avoid unnecessarily long reasoning or output.