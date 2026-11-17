"""One-time migration to data model 1: the temporal model (spec 2026-11-15 §8).

TEMPORARY. Core code knows only the new model; everything that converts a vault
made before it lives here and is reached through `artmind migrate`. Nothing
outside this package imports it except one `cli.add_command(migrate_group)` line
in `artmind/cli.py`.

Sunset: delete `artmind/migrations/temporal_v1/`, `test/migrations/temporal_v1/` and
that `add_command` line once `artmind migrate status` reports `version: 1` and no
pending step in every vault. `artmind/data_model.py` stays.
"""
