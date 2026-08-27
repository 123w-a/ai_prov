# Project Benchmarking

This project has two separate proof layers:

1. `benchmarks/run_baseline.py --offline` measures the real `kb_corpus` PDFs and executes historical deterministic guardrail cases.
2. `benchmarks/recall.py` measures case-level recall@k of the live RAG retriever against `benchmarks/retrieval_cases.py` (keywords harvested from real corpus hits on 2026-08-27; baseline recall@3 = 1.0, floor 0.75). It prints `SKIPPED` with a reason when the Chroma collection is empty or models fail to load, and exits non-zero only on a real recall regression.
3. `tests/validation_matrix.json` declares the commands that may become delivery evidence.

The matrix does not accept a chat claim or a JSON file produced by the model as proof. The host must emit and register a `tools/result` receipt for the exact command. `benchmarks.validation` checks receipt identity fields, command binding, outcome, and replay inside a matrix; it is not a signer. Authenticity and one-time global consumption remain the responsibility of the host receipt registry (currently exercised through `sp_verify`/`sp_done`).

The current corpus baseline is 27 PDFs, 682 pages, and 443,322 extracted characters. The four executable historical cases cover high-risk gout menus, salt limits, pregnancy traceability, and a clean menu.

RAG tests are non-blocking when the local embedding model or Chroma index is absent. They are skipped with an explicit reason; they are not reported as passed. A CI or nightly job should provision the model/index and require those tests to run.

Suggested host flow:

```text
read tests/validation_matrix.json
for each item:
  execute exact item.command
  receive tools/result
  validate receipt source + command + exitCode + timeout + sandbox
  parse machine output and evaluate thresholds
  mark item passed/failed/blocked
```
