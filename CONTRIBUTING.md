# Contributing

Thanks for your interest in improving PrismaOwl!
Contributions of all kinds are welcome: bug reports, feature requests,
documentation improvements, and code.

## Reporting bugs and requesting features

Please use the [GitHub issue tracker](https://github.com/0xj0hannes/PrismaOwl/issues).
When reporting a bug, include:

- what you did (command or steps),
- what you expected to happen,
- what actually happened (including the full error message / traceback),
- your operating system and Python version.

For LLM-related issues, please also note your `MODEL_NAME` and, where relevant,
the contents of `logs/screening.log` (with any sensitive data removed).

## Development setup

```bash
git clone https://github.com/0xj0hannes/PrismaOwl.git
cd PrismaOwl
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Running the tests

The test suite runs offline — no API key is required (the LLM client and all
database HTTP calls are mocked).

```bash
pytest
```

Please make sure the tests pass before opening a pull request, and add tests for
any new behaviour.

## Submitting changes

1. Fork the repository and create a topic branch from `main`.
2. Make your change, keeping the style consistent with the surrounding code.
3. Add or update tests and documentation as needed.
4. Ensure `pytest` passes.
5. Open a pull request describing the change and the motivation for it.

## Code of conduct

By participating in this project you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Getting help

If you have a usage question rather than a bug report, please open a
[GitHub issue](https://github.com/0xj0hannes/PrismaOwl/issues)
with the `question` label.
