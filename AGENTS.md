# Project conventions

- The primary Git branch is always `master`.
- Every commit must use Conventional Commits and Gitmoji. Put the emoji after
  the type and optional scope: `feat: ✨ add migration support`.
- Use English for code comments, docstrings, identifiers, variable names,
  function names, CLI messages and commit descriptions, even when discussing
  the work in German.
- Parameterize environment-specific values. Never hardcode usernames, profile
  paths, workspace counts, credentials or machine-specific assumptions.
- Use descriptive variables and named constants for repeated configuration
  values. Fixed Vivaldi format keys and synthetic test fixtures are protocol
  data, not user configuration.
- Keep browser profile data, generated reports, backups and credentials out of
  Git history. Stage source files explicitly.
- Keep the tool dependency-free unless the task explicitly requires otherwise.
- Run `python3 -m unittest discover -s tests -v` after behavioral changes.
- Never run migration tests against an installed browser profile. Use synthetic
  temporary profiles for automated tests.
