# Python Runtime and Testing

## Target runtime
- **Target Python runtime: 3.11**

## Local test command (Windows)
- `\.venv\Scripts\python.exe -m pytest -q`

## Notes for contributors
- In some automation/container environments, a newer Python version (for example 3.14) may be present.
- If route-registration or app-import tests fail due to FastAPI/Pydantic compatibility under non-target runtimes, treat that as an environment mismatch, not an automatic regression in branch logic.
- Do **not** change production code solely to satisfy non-target runtime behavior unless explicitly requested.
