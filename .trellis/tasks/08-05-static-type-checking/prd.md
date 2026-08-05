# Adopt a type checker across the package

## Context

No type checker has ever run over this codebase. Type hints on every argument and return value
are required by review, but nothing verifies them, so the annotations are unchecked documentation.

This was surfaced during the consolidation, when the plan's validation command called
`pixi run typecheck` — a task that does not exist. Retrofitting a checker mid-port would have
surfaced hundreds of pre-existing findings and turned a consolidation into a typing project, so
it was deferred rather than skipped.

## Requirements

- Pick one checker and say why in an ADR. `mypy --strict` and `pyright` are both defensible;
  running two is not.
- Land it in **`pixi run typecheck`**, in CI, and in pre-commit — matching how `lint`,
  `format-check`, and the three structure checks are already wired.
- Baseline the existing findings rather than fixing them all in one commit. A per-module ignore
  list that shrinks is progress; a blanket `ignore_errors` is not, because it never shrinks.

## Acceptance Criteria

- [ ] `pixi run typecheck` exits 0.
- [ ] The baseline is a finite, enumerated list of modules — not a global suppression.
- [ ] A planted type error fails CI, proving the check is wired rather than merely present.

## Notes

Do this after the consolidation lands. Type-checking a tree that is still moving means fixing the
same error twice.
