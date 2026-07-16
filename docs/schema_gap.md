# Schema Gap Analysis

Comparison between v1.2 data contracts (docs/02_DATA_CONTRACTS.md) and the current slot_atlas.db schema.

## 1. Tables: Current vs Required

### Tables that exist — need migration

| v1.2 Table | Current Table | Status |
|---|---|---|
| halls | halls | Column additions needed |
| hall_days | hall_days | Column additions + rename |
| machine_days | machine_days | Column additions + rename |
| tail_days | tail_days | Column additions + rename |
| unit_days | unit_days | Column additions + rename (0 rows) |
| predictions | predictions | Major restructure |

### Tables that do not exist — need creation

| v1.2 Table | Current Equivalent | Gap |
|---|---|---|
| raw_sources | source_snapshots (partial) | New table. source_snapshots has URL/hash but no acquisition_method, parser_version, raw_path, parent_raw_source_id |
| machines | (none) | machine_key/machine_name are inline in machine_days. No master table |
| hall_aliases | (none) | No alias resolution system |
| machine_aliases | (none) | No alias resolution system |
| unit_outcomes | (none) | Q_unit composite scoring doesn't exist |
| hall_capabilities | (none) | FULL/SUMMARY/NONE exists in free_source_predictor but not as DB table |
| event_families | (none) | evidence_rules.match_json handles date patterns but no family_id concept |
| prediction_runs | model_runs (partial) | model_runs lacks feature_cutoff_at, config_version, source_snapshot_hash, feature_snapshot_hash, status, published_payload_hash |
| outcomes | (none) | No result-tracking separate from predictions |
| hypotheses | evidence_rules + validation_log (partial) | evidence_rules is flat; no parent/child lineage, no reframed status, no required_capabilities_json |
| chain_pattern_results | (none) | No chain/multi-hall correlation tables |
| layouts | (none) | No layout/neighbor graph |
| source_revisions | (none) | No revision tracking |

## 2. Per-Table Column Gaps

### halls

| v1.2 Column | Current Column | Gap |
|---|---|---|
| hall_id | hall_id | OK |
| canonical_name | name | Rename |
| chain_id | (none) | **ADD** |
| region_id | (none) | **ADD** — current has `market` which is similar |
| active_from | (none) | **ADD** |
| active_to | (none) | **ADD** |
| — | market, active, forecast_enabled, forecast_block_reason, slot_count, exchange_label, decision_floor, travel_*, baseline_*, data_through, source_*, notes, reset_policy | Extra columns to preserve |

Migration strategy: ALTER TABLE ADD for new columns. Keep existing columns for backward compatibility.

### hall_days

| v1.2 Column | Current Column | Gap |
|---|---|---|
| hall_id | hall_id | OK |
| business_date | result_date | Semantic rename (alias or view) |
| event_family_id | (none) | **ADD** — currently derived from evidence_rules at query time |
| avg_diff | avg_diff | OK |
| total_diff | total_diff | OK |
| total_games | avg_games | OK (rename semantics — current stores avg, v1.2 says total) |
| observed_units | (none) | **ADD** — partial: winning_units + total_units exist |
| installed_units | (none) | **ADD** |
| rank_actual | (none) | **ADD** |
| source_raw_id | source_name + snapshot_id | Map to raw_sources FK |
| is_final | (none) | **ADD** (default 0) |

### machine_days

| v1.2 Column | Current Column | Gap |
|---|---|---|
| hall_id | hall_id | OK |
| business_date | result_date | Semantic rename |
| machine_id | machine_key | Rename + link to machines master |
| units | units | OK |
| observed_units | (none) | **ADD** |
| avg_diff | avg_diff | OK |
| positive_rate | (none) | **ADD** — derivable from winning_units/total_units |
| avg_games | avg_games | OK |
| coverage | (none) | **ADD** |
| q_machine | (none) | **ADD** |
| event_selected_label | selected_flag | Rename; selected_flag is from hall source, event_selected_label is analytical |
| organic_active_day | (none) | **ADD** |
| organic_selected_label | (none) | **ADD** |
| label_status | (none) | **ADD** (default 'unknown') |
| source_raw_id | source_name + snapshot_id | Map to raw_sources FK |

### tail_days

| v1.2 Column | Current Column | Gap |
|---|---|---|
| hall_id | hall_id | OK |
| business_date | result_date | Semantic rename |
| tail | tail_key | Rename (key→string) |
| units | (none) | **ADD** |
| observed_units | (none) | **ADD** |
| avg_diff | avg_diff | OK |
| positive_rate | (none) | **ADD** — derivable from winning_units/total_units |
| avg_games | avg_games | OK |
| coverage | (none) | **ADD** |
| source_raw_id | source_name + snapshot_id | Map to raw_sources FK |

### unit_days

| v1.2 Column | Current Column | Gap |
|---|---|---|
| hall_id | hall_id | OK |
| business_date | result_date | Semantic rename |
| unit_no | unit_no | OK (current INTEGER, v1.2 TEXT) |
| machine_id | machine_name | Rename + link to machines master |
| diff | diff | OK |
| games | games | OK |
| bb_count | (none) | **ADD** |
| rb_count | (none) | **ADD** |
| at_count | (none) | **ADD** |
| cz_count | (none) | **ADD** |
| initial_hit_count | (none) | **ADD** |
| source_raw_id | source_name | Map to raw_sources FK |
| evidence_completeness | (none) | **ADD** |

### predictions (major restructure)

| v1.2 Column | Current Column | Gap |
|---|---|---|
| prediction_run_id | run_id | Rename + type change (TEXT vs INTEGER) |
| target_date | target_date | OK |
| hall_id | hall_id | OK |
| entity_type | (none) | **ADD** — current only has hall-level predictions |
| entity_id | (none) | **ADD** — current uses hall_id as implicit entity |
| score | predicted_mean | Rename + reinterpret (was raw diff, now 0-100) |
| rank | rank | OK but different semantics (S/A/B/C vs integer) |
| confidence | confidence | OK |
| explanation_json | reasons_json | Rename |
| warnings_json | (none) | **ADD** |
| capability_json | (none) | **ADD** |
| — | adjusted_edge, utility_edge, travel_minutes, travel_penalty, rule_id | Extra columns to preserve for backward compat |

## 3. Concept Gaps

### event_family_id

**Current**: Date-pattern matching is done via `evidence_rules.match_json` at query time. The `family_key()` function in free_source_predictor.py derives family labels from rule text heuristically.

**Required**: Explicit `event_families` table with `event_family_id`, `hall_id`, `family_type`, `rule_json`, `confidence`.

**Migration**: Extract unique family patterns from evidence_rules, create event_families entries, backfill event_family_id into hall_days.

### Source lineage and raw_sources

**Current**: `source_snapshots` stores fetch metadata (URL, SHA-256, parse status). No acquisition_method, parser_version, or raw file paths.

**Required**: Full `raw_sources` table with immutable storage, acquisition classification, and parser versioning.

**Migration**: Transform source_snapshots into raw_sources records. Add acquisition_method='automated_public' for existing data.

### Prediction run immutability

**Current**: Single `model_runs` row. No freeze/status/hash tracking. Predictions can be updated freely.

**Required**: `prediction_runs` with status lifecycle (draft→frozen→published→superseded), source/feature snapshot hashes, immutability guard on frozen runs.

**Migration**: Create prediction_runs table. Migrate existing model_runs record. Add trigger or application-level guard for frozen status.

### Machine master

**Current**: machine_key and machine_name are denormalized across machine_days (20K rows), machine_scores (617 rows), position_signals (115 rows).

**Required**: Centralized `machines` table with machine_id, canonical_name, category, machine_version.

**Migration**: Extract unique machines from machine_days, create master entries, update FKs.

### Capability system

**Current**: FULL/SUMMARY/NONE layer in free_source_predictor.py, computed at build time based on row counts. Not stored in DB.

**Required**: `hall_capabilities` table with per-capability boolean flags, stored per hall per date.

**Migration**: Create table, compute capabilities from existing data coverage per hall.

### Hypotheses

**Current**: `evidence_rules` (410 rules, flat) + `validation_log` (245 entries, all pending). No parent/child lineage, no reframed status.

**Required**: `hypotheses` table with status lifecycle (active→validated→rejected→killed→reframed), parent-child links, required_capabilities_json.

**Migration**: Convert evidence_rules to hypotheses with legacy_source/legacy_id. Convert validation_log claims to hypothesis links.

### Chain patterns

**Current**: free_source_predictor.py has basic joint multi-hall detection (pattern #8) comparing machine selections across halls on same dates.

**Required**: Full `chain_pattern_results` table with 4 independent pattern types (joint_machine, machine_split, date_role_split, intensity_split), each with permutation tests and p-values.

**Migration**: Create table. Enhance existing joint detection. Add 3 new pattern detectors.

## 4. Column Naming Conventions

| Current | v1.2 | Notes |
|---|---|---|
| result_date | business_date | All date tables |
| machine_key | machine_id | All machine references |
| tail_key | tail | tail_days |
| source_name | source_raw_id | All source references |
| name (halls) | canonical_name | halls |
| selected_flag | event_selected_label | machine_days |

Recommendation: Use views or column aliases for backward compatibility during transition. Do not rename existing columns in-place to avoid breaking slot_atlas.py and atlas_plus.py which read these columns directly.

## 5. Data Type Changes

| Table.Column | Current Type | v1.2 Type | Risk |
|---|---|---|---|
| unit_days.unit_no | INTEGER | TEXT | Low (0 rows currently) |
| predictions PK | (run_id INTEGER, target_date, hall_id) | (prediction_run_id TEXT, target_date, hall_id, entity_type, entity_id) | High — PK structure change |
| model_runs.run_id | INTEGER AUTOINCREMENT | prediction_run_id TEXT | Medium — new table recommended |

## 6. Migration Risk Assessment

| Migration | Risk | Mitigation |
|---|---|---|
| predictions restructure | HIGH | Create new predictions_v2 table alongside existing. Keep old predictions table for backward compat |
| model_runs → prediction_runs | MEDIUM | New table, keep model_runs |
| halls column additions | LOW | ALTER TABLE ADD COLUMN |
| machine_days additions | LOW | ALTER TABLE ADD COLUMN |
| New tables (12 tables) | LOW | Pure additions, no existing data affected |
| event_family backfill | MEDIUM | Derive from evidence_rules; may not cover all date patterns |
| machine master extraction | MEDIUM | Deduplication of machine_name variants across sources |
