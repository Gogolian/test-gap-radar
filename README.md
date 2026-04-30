# test-gap-radar

Maps production-risk signals and changed code to the missing tests that would
reduce the most risk.

## MVP

```bash
test-gap-radar analyze
```

The analyzer ranks source files using:

- changed files from recent Git history
- bug-fix or incident commits
- coverage from `coverage.json`, `coverage/coverage-summary.json`, or `lcov.info`
- critical path keywords such as billing, payment, auth, and security
- ownership from `CODEOWNERS`
- simple branch/size complexity
- matching test files
- flaky or disabled test references

Example output:

```txt
Highest-value missing test:
src/billing/proration.ts

Reasons:
- Changed 9 times in the selected window
- 3 bug-fix commits mention this file
- Current coverage: 22%
- Critical path keyword detected
```

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests
```
