# Backend Tests

`run_all.py` is the single entry point and discovers every `test_*.py` below this directory.

- `agent/`: graph orchestration, turn decisions, streaming, image flow, and model adapters.
- `api/`: HTTP routes and request/response contracts.
- `domain/`: allergen, nutrition, family, profile, and schema rules.
- `rag/`: parsing, chunking, retrieval, citations, and recall metrics.
- `services/`: service-layer adapters such as vision and dish assets.
- `storage/`: sessions, feedback, preferences, and atomic file persistence.
- `integration/`: cross-module loops, live smoke checks, and validation receipts.

Run all backend tests from the repository root:

```powershell
.venv\Scripts\python.exe tests\run_all.py
```
