# Contributing

## Dev setup

```bash
git clone https://github.com/exampleuser/HouseholdBudgetHelper.git
cd TelegramBudgetHelper
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in tokens
cp config.example.json config.json   # fill in model/user/account
```

See [docs/setup.md](docs/setup.md) for the full setup walkthrough.

## Running tests

```bash
pytest                          # all tests
pytest tests/test_excel_reader.py   # one file
pytest tests/test_config.py::test_config_loads_defaults  # one test
```

All external calls (LLM, Yandex Disk, Langfuse) are mocked. No real tokens needed to run the suite. See [docs/testing.md](docs/testing.md) for the eval harness.

## Code style

- Python 3.12+, async throughout
- Pydantic v2 for structured LLM output
- No inline comments unless the **why** is non-obvious
- No personal data, tokens, or real financial figures in code, tests, or docs

## Key rules

- **Generic:** no hardcoded names, accounts, or household-specific logic — read from `config.json` or Excel
- **Security:** never commit secrets; `.env` and `config.json` are gitignored. See [docs/security.md](docs/security.md).
- **Docs in sync:** update `AGENTS.md` (and affected docs in `docs/`) after every change to architecture, config, or commands

## Pull requests

- One logical change per PR
- Tests must pass (`pytest`) — CI runs automatically
- Update `AGENTS.md` and relevant `docs/` files if the architecture, config, or command list changed
