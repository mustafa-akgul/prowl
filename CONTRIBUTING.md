# Contributing

Thanks for your interest in improving PRowl!

## Development setup

```bash
git clone https://github.com/<your-username>/prowl.git && cd prowl
pip install -r requirements.txt
pip install -e ".[dev]"
cp .env.example .env        # fill in the keys you need
pre-commit install
```

Run the dashboard with `python main.py` and the tests with `pytest tests/ -v`.

## Code conventions

- `from __future__ import annotations` at the top of every module
- Google-style docstrings (`Args` / `Returns` / `Raises`) on all public
  functions and classes
- One custom exception per integration module (`GitHubError`, `JiraError`,
  `GeminiError`, …) inheriting from `LLMError`
- All code, comments, log messages, and UI text are in **English**
- Formatting and linting are enforced with **Ruff** (`ruff format`,
  `ruff check`) — configuration lives in `pyproject.toml`

## Test conventions

- No shared `conftest.py` — each test file defines its own `mock_env` fixture
- Patch the **consumer's import path**, not the origin module
  (e.g. `agent.review_agent.GeminiClient`, not `agent.gemini_client.GeminiClient`)
- Tests must not hit the network; all externals are mocked

## Adding a new review mode

1. Create a prompt template under `agent/prompts/` using the
   `{jira_context}`, `{pm_instructions}`, and `{diff}` placeholders
2. Register the mode in `MODE_TO_FILE` in `agent/config_manager.py`
3. Add the mode to the `--mode` choices in `agent/review_agent.py`
4. Extend the mode regex in `.github/workflows/pm-command.yml`
5. Add at least one test under `tests/`

## Pull requests

- Open an issue first for non-trivial changes (see [docs/ROADMAP.md](docs/ROADMAP.md))
- Keep PRs focused; include tests for behavior changes
- Make sure `ruff check`, `ruff format --check`, and `pytest` pass locally —
  CI runs the same checks
