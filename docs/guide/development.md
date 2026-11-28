# Development

[Easy TRV](../../README.md) › [Documentation](README.md)

## Development

Python 3.14.2 or newer and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync                      # install the dependencies, including Home Assistant
uv run pytest                # tests with a coverage report
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # strict typing of core/, profiles/ and adapters/
```

CI runs the same checks plus hassfest and the HACS validation. Testing on real thermostats is covered by [`docs/testing-on-device.md`](../testing-on-device.md); the technical design is in [`docs/specs/2026-10-12-easy-trv-design.md`](../specs/2026-10-12-easy-trv-design.md).
