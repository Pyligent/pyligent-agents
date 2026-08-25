## What breaks without this change?

<!-- One or two sentences. If nothing breaks, say what it improves and why. -->

## Type

- [ ] Bug fix
- [ ] New gate / stop condition / backend
- [ ] New example
- [ ] Documentation
- [ ] Other:

## If this touches agent behaviour, the four questions

<!-- Delete this block if it does not. -->

| | |
|---|---|
| Stop condition | |
| Who verifies | |
| Spend cap | |
| On failure | |

## Checklist

- [ ] `pytest` passes offline, with no credential
- [ ] `ruff check src tests examples` is clean
- [ ] Every new guardrail has a test that **fails when the guardrail is removed**
- [ ] At least one failure path is covered, not only the happy path
- [ ] Comments explain *why*, and any limits of a new control are stated honestly
- [ ] `CHANGELOG.md` updated under `[Unreleased]` if user-visible
