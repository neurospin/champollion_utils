# Changelog

## [Unreleased]

## [0.4.3] - 2026-11-16

### Changed

- ScriptBuilder's print_args, execute_command and validate_paths messages go
  through the champollion_utils logger (the command line at LOG level, errors
  at ERROR) instead of being printed on stdout. Tracked in the pipeline ledger
  as TASK-LOGGING-BDRABCZUK-0D3FE48FC54E (commit 73b4ee4).
