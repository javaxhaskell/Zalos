# Safety Repair Latency Diagnosis — `22c0c771-4df3-45d9-a0b7-89430f35749f`

## Summary

- Root unsafe construct: `eval()` in `generated/agent.py`
- Source of `eval()`: initial code generation, not repair
- Safety repair saw the exact violation: `{"generated/agent.py": ["eval()"]}`
- Safety repair failed because its response was truncated/malformed and still contained `eval()`
- Follow-up JSON repair also failed to produce a valid safe patch
- Final failure was correct, but it arrived too slowly

## Evidence

### Where `eval()` entered

`generated/model_responses/code_generation.txt` contains:

```python
if eval(rule['condition'], {'row': row}):
```

So the unsafe construct came from initial code generation.

### What safety repair received

`generated/model_responses/safety_repair.prompt.txt` includes:

- `unsafe_findings: {"generated/agent.py": ["eval()"]}`
- the full generated agent
- the full contract

The prompt did identify the exact forbidden construct, but it was oversized.

### What safety repair returned

`generated/model_responses/safety_repair.txt`:

- hit the configured cap exactly: `output_tokens=2048`
- remained unsafe: it still contained `eval(rule['condition'], {'row': row})`
- was malformed enough to trigger `json_parse_failed`

### What JSON repair returned

`generated/model_responses/safety_repair.json_repair.txt`:

- also remained unsafe
- was still malformed enough to trigger another `json_parse_failed`

So JSON repair did not remove `eval()` and did not produce an acceptable patch envelope.

## Timing

Derived from `events.jsonl` model start/end timestamps:

- `contract_planning`: ~298.5s
- `contract_review`: ~299.4s
- `code_generation`: ~58.4s
- `test_generation`: ~51.4s
- `json_repair` for `test_generation`: ~101.7s
- `safety_repair`: ~102.2s
- `json_repair` for `safety_repair`: ~216.8s

The longest calls were planning and review overall. Inside the failing safety branch, the dominant cost was the final `json_repair` call on malformed `safety_repair` output.

## Why it took ~20 minutes

1. Planning and review each consumed about five minutes on local Ollama.
2. Test generation was malformed and needed its own JSON repair.
3. Safety repair then used an oversized prompt and returned a capped 2048-token response.
4. A generic 14b JSON repair spent another ~217s trying to reconstruct that malformed safety-repair payload.
5. Only after that second parse failure did the workflow fail.

The 30-second heartbeat events did not hide latency; they accurately reflected long-running model calls.

## Why the system did not fail faster

The old safety path had three structural problems:

1. It sent too much context into `safety_repair`:
   - full contract
   - unsafe file
   - safe test file
2. It asked the model to regenerate unsafe files, but not with tight path/snippet-specific guidance.
3. On malformed safety-repair output, it used a generic JSON repair prompt that operated on a large malformed payload instead of a narrow envelope-only repair.

## Fix direction implemented

- Narrow `safety_repair` to unsafe files only
- Include exact path, construct, and snippet context
- Explicitly forbid `eval()`, `exec()`, `compile()`, dynamic import, `os.system`, shell calls, and unsafe substitutions
- Add a clearer fail-fast result when safety repair returns invalid JSON or when the forbidden construct remains after recheck
- Make safety-stage JSON repair envelope-only so it does not silently rewrite code
- Record `duration_ms` on `model_called` events for direct latency diagnosis
