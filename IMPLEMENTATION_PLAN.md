# Implementation Plan — FREE_PUBLIC_MVP

Based on investigation of 2026-07-16. See `docs/current_state_inventory.md` and `docs/schema_gap.md` for full details.

## Investigation Summary

### What exists

- **66 halls**, 410 evidence_rules, 24,090 predictions (365-day forecasts)
- **20,229 machine_days**, 2,475 tail_days, 617 machine_scores, 115 position_signals
- **0 unit_days** (table exists, no data — requires paid/field sources)
- Build pipeline: slot_atlas.py generate → build_site_data.py → encrypt_vault.mjs → GitHub Pages
- Free source predictor with FULL/SUMMARY/NONE layers, 15-pattern catalog, machine rotation, tail z-scores
- Validation queue (245 pending claims), no automated evaluation
- Python stdlib-only, Node.js built-in-only, no package.json

### What does not exist

- prediction_runs / freeze / immutability / canonical JSON / SHA-256 hashing
- warnings field on predictions
- event_family_id / event_families table
- raw_sources / source lineage / acquisition_method
- machines master table / machine_aliases / hall_aliases
- outcomes table (prediction-result separation)
- hypotheses with parent-child lineage
- hall_capabilities table
- chain_pattern_results (4-type detector)
- unit_outcomes / Q_unit composite scoring
- layouts / neighbor graph
- config_version

### Key constraints

- Python stdlib-only (no numpy, pandas, scipy)
- No build step in CI — all build/encrypt is local before commit
- Existing slot_atlas.py and atlas_plus.py must continue to work unchanged
- Vault encryption preserves salt for cached login sessions
- unit_distribution_policy = local_only (no unit data in vault)

---

## Phase 0 — Prediction Freezing

**Goal**: Immutable prediction runs with canonical JSON, SHA-256, outcomes, and warnings.

### Files to create

| File | Purpose |
|---|---|
| `tools/migrate_db.py` | Idempotent DB migration script |
| `tools/build_predictions.py` | Feature builder + prediction generator (--source-mode free_public) |
| `tools/freeze_run.py` | Freeze a draft prediction run → canonical JSON + SHA-256 |
| `tools/evaluate_predictions.py` | Join frozen predictions with outcomes |
| `predictions/frozen/` | Directory for frozen run files |
| `tests/test_prediction_run.py` | Phase 0 acceptance tests |

### DB migrations

```sql
-- New tables
CREATE TABLE IF NOT EXISTS prediction_runs (...);   -- per 02_DATA_CONTRACTS.md §11
CREATE TABLE IF NOT EXISTS outcomes (...);           -- per §13

-- Extend existing
ALTER TABLE predictions ADD COLUMN entity_type TEXT;
ALTER TABLE predictions ADD COLUMN entity_id TEXT;
ALTER TABLE predictions ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE predictions ADD COLUMN capability_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE predictions ADD COLUMN explanation_json TEXT NOT NULL DEFAULT '[]';
```

Note: New predictions use the v1.2 schema (prediction_run_id TEXT PK, entity_type/entity_id). Existing predictions (run_id=1) remain untouched in the old predictions table. New predictions go to a separate `predictions_v2` table to avoid breaking `slot_atlas.py generate`.

### Implementation

1. `migrate_db.py`: Idempotent CREATE TABLE IF NOT EXISTS for all new tables. ALTER TABLE ADD COLUMN wrapped in try/except for idempotency.
2. `freeze_run.py`: Read draft JSON → validate schema (all predictions have warnings) → canonical JSON (keys sorted, no extra whitespace, UTF-8, LF) → SHA-256 → write to predictions/frozen/ → optionally INSERT to prediction_runs with status='frozen'.
3. Immutability guard: Application-level check in freeze_run.py — refuse to overwrite existing frozen file. In DB, reject UPDATE on prediction_runs WHERE status IN ('frozen','published').
4. `evaluate_predictions.py`: Load outcomes from post-result data. Join with frozen predictions by (target_date, hall_id, entity_type, entity_id). Write evaluation report. Never modify prediction records.

### Acceptance tests (from 03_ACCEPTANCE_TESTS.md)

- P0-01: 1 run, multiple target_dates ✓
- P0-02: target_date in predictions, not prediction_runs ✓
- P0-03: warnings required on all predictions ✓
- P0-04: Deterministic: same input → same canonical JSON + SHA-256 ✓
- P0-05: Frozen run UPDATE rejected ✓
- P0-06: Outcome insertion doesn't change prediction hash ✓
- P0-07: Feature with date >= feature_cutoff_at → build failure ✓
- P0-08: 1-byte source change → different source_snapshot_hash ✓
- P0-09: Failed vault verify → old vault preserved ✓

### Risks

- **LOW**: Existing predictions table untouched; new system uses separate table
- **LOW**: migrate_db.py is additive-only, no destructive changes

### Backward compatibility

- slot_atlas.py `generate` continues to write to old predictions table
- build_site_data.py continues to read from exports/forecast_candidates_365.csv
- Vault format unchanged

---

## Phase 1A — Normalization and Source Lineage

**Goal**: Centralized ID resolution, source tracking, event families.

### Files to create/modify

| File | Purpose |
|---|---|
| `tools/migrate_db.py` | Add raw_sources, machines, hall_aliases, machine_aliases, event_families, hall_capabilities |
| `tools/normalize_sources.py` | Populate raw_sources from source_snapshots; assign acquisition_method |
| `tools/build_event_families.py` | Extract event_families from evidence_rules.match_json patterns |
| `tools/build_capabilities.py` | Compute hall_capabilities from data coverage |

### DB migrations

```sql
CREATE TABLE IF NOT EXISTS raw_sources (...);        -- per §2
CREATE TABLE IF NOT EXISTS machines (...);           -- per §3
CREATE TABLE IF NOT EXISTS hall_aliases (...);       -- per §3
CREATE TABLE IF NOT EXISTS machine_aliases (...);    -- per §3
CREATE TABLE IF NOT EXISTS event_families (...);     -- per §10
CREATE TABLE IF NOT EXISTS hall_capabilities (...);  -- per §9

-- Backfill columns
ALTER TABLE hall_days ADD COLUMN event_family_id TEXT;
ALTER TABLE machine_days ADD COLUMN coverage REAL;
ALTER TABLE machine_days ADD COLUMN label_status TEXT DEFAULT 'unknown';
```

### Implementation

1. Extract machines master from machine_days (dedupe on machine_key). Use existing machine_name as canonical_name.
2. Derive event_families from evidence_rules: group rules by (hall_id, match_json pattern). Assign family_type from existing family_key() logic in free_source_predictor.py.
3. Backfill event_family_id into hall_days by matching each (hall_id, result_date) against event_families rules.
4. Compute hall_capabilities from counts: hall_daily = hall_days rows > 0, machine_daily = machine_days rows > 0, etc.
5. Transform source_snapshots → raw_sources with acquisition_method='automated_public'.

### Risks

- **MEDIUM**: Event family extraction from free-text evidence_rules.label may miss some patterns. Mitigation: manual review of unmapped rules, set event_family_id=NULL for uncertain cases.
- **LOW**: Machine deduplication — machine_key is already normalized.

---

## Phase 1B — Machine Labels

**Goal**: event_selected_label and organic_active_day as separate analytical labels.

### Files to modify

| File | Purpose |
|---|---|
| `tools/build_predictions.py` | Add label computation |
| `tools/migrate_db.py` | Add label columns to machine_days |
| `tests/test_machine_labels.py` | Label acceptance tests |

### DB migrations

```sql
ALTER TABLE machine_days ADD COLUMN event_selected_label INTEGER;
ALTER TABLE machine_days ADD COLUMN organic_active_day INTEGER;
ALTER TABLE machine_days ADD COLUMN organic_selected_label INTEGER;
ALTER TABLE machine_days ADD COLUMN q_machine REAL;
ALTER TABLE machine_days ADD COLUMN positive_rate REAL;
```

### Implementation

Per 00_MASTER_DESIGN_v1.2.md §6:

1. **event_selected_label**: Within same event_family_id days only. Criteria: units >= 2, coverage >= 60%, Q_machine top 15% or >= 1.0, avg_diff > 0. Unknown if result missing.
2. **organic_active_day**: Absolute gate — any machine with avg_diff >= 800, positive_rate >= 0.70, units >= 2, coverage >= 0.60. Not relative ranking.
3. **organic_selected_label**: Only on organic_active_day=1. Same label criteria as event but using normal-day comparisons only.

### Acceptance tests

- P1-01: Event model uses same-family rows only ✓
- P1-02: Missing results → label stays NULL, not 0 ✓
- P1-03: Organic gate is absolute, not relative ✓
- P1-04: Organic model needs 20+ valid days and activation_rate >= 0.20 ✓

### Risks

- **MEDIUM**: positive_rate computation needs winning_units/total_units which exist but may have NULLs. Mitigation: NULL → unknown, not 0.

---

## Phase 1C — Machine Prediction and Top 5

**Goal**: Score machines, produce Top 5 per hall per event day.

### Files to modify

| File | Purpose |
|---|---|
| `tools/build_predictions.py` | Feature builder, scoring, Top5 |
| `tools/free_source_predictor.py` | May integrate or replace existing machine scoring |
| `tests/test_machine_prediction.py` | Scoring and Top5 tests |

### Implementation

Per 00_MASTER_DESIGN_v1.2.md §6.4-6.5:

Feature vector per machine:
- event selection rate: `(hits + 1) / (eligible + 4)` (Laplace)
- rotation fit: `clip((days_since - median_gap) / MAD, -2, 2)`
- last excluded: binary flags last_1_selected, last_2_selected
- size fit: unit bin hit rate vs hall rate, log odds
- weekday fit: event_family × weekday smoothed rate
- recent demand: avg_games percentile
- chain signal: weak auxiliary (from Phase 1.5)

Score: `machine_score = 100 × sigmoid(L)` where L is weighted sum (1.20 × logit(p_event) + 0.55 × rotation + ...).

Top5: Rank by score within (hall_id, target_date, event_family_id). Output max 5. Capability-less halls get explicit "機種データなし".

Publish horizon: max 21 days or next 2 same-family events.

### Acceptance tests

- P1-05: Top5 output for capable halls, explicit "no data" for incapable ✓
- P1-06: Score 0-100 ✓
- P1-07: No calibrated_probability if family samples < 30 ✓
- P1-08: Publish horizon enforced ✓

### Risks

- **MEDIUM**: Integration with existing free_source_predictor scoring. Current scoring uses different weights (0.52/0.30/0.18 vs v1.2's 1.20/0.55/0.45/...). Strategy: build_predictions.py uses v1.2 formula for new predictions; free_source_predictor.py's scoring remains for display continuity until Tenjikai UI is updated to consume new predictions.

---

## Phase 1D — Tail Analysis

**Goal**: Shrinkage z-scores per tail per event family.

### Files to modify

| File | Purpose |
|---|---|
| `tools/build_predictions.py` | Tail z computation |
| `tests/test_tail_analysis.py` | Shrinkage and family-filtering tests |

### Implementation

Per 00_MASTER_DESIGN_v1.2.md §7:

```
residual(d, t) = tail_avg_diff(d, t) - hall_avg_diff(d)
z_raw(t) = mean_residual(t) / max(SE(t), epsilon)
shrink(t) = n_eff(t) / (n_eff(t) + 8)
z_shrunk(t) = shrink(t) × clip(z_raw(t), -4, 4)
```

Same-family only. No date-pun (日付こじつけ) without explicit warning.

### Acceptance tests

- T-01: Same-family filtering ✓
- T-02: Shrinkage applied ✓
- T-03: No date-pun strong judgments ✓
- T-04: Warning on untested hypotheses ✓

### Risks

- **LOW**: tail_days has 2,475 rows across ~50 halls. Some halls may have insufficient tail data → NONE for tail capability. This is correct behavior.

---

## Phase 1.5 — Chain Store Correlation (4-type)

**Goal**: Independent detectors for joint_machine, machine_split, date_role_split, intensity_split.

### Files to create

| File | Purpose |
|---|---|
| `tools/chain_detector.py` | 4-type chain pattern detection with permutation tests |
| `tools/migrate_db.py` | Add chain_pattern_results table |
| `tests/test_chain_detector.py` | Per-type acceptance tests |

### DB migration

```sql
CREATE TABLE IF NOT EXISTS chain_pattern_results (...);  -- per §15
```

### Implementation

Per 00_MASTER_DESIGN_v1.2.md §11:

1. **joint_machine**: Co-selection lift with permutation test (10,000 shuffles). Promote if common_days >= 8, lift >= 2.0, p < 0.05.
2. **machine_split**: Detect complementary machine allocation across chain members.
3. **date_role_split**: Event family × hall intensity matrix. Detect role concentration vs baseline.
4. **intensity_split**: Standardized hall-day intensity. Detect negative correlation within chain (one strong, other weak).

Requires chain_id in halls table. Currently, no chain_id exists — need to derive or manually assign.

### Acceptance tests

- C-01: 4 types stored as separate records ✓
- C-02: joint_machine has permutation test ✓
- C-03: No strong promotion with < 8 common days ✓
- C-04: No circular dependency (chain signal → label → chain signal) ✓

### Risks

- **HIGH**: chain_id does not exist in current halls table. 66 halls need chain assignment. Some halls are independent (chain_id=NULL). Strategy: Add chain_id column to halls, populate from hall names (マルハン→maruhan, エスパス→espace, etc.) with manual review. Independent halls get chain_id=NULL and are excluded from chain analysis.
- **MEDIUM**: Permutation tests (10,000 iterations) in Python stdlib may be slow. Mitigation: random module is sufficient; limit to chain pairs with >= 8 common days.

---

## Phase 1.75 — Unit Layer (Optional)

**Goal**: Q_unit, high_proxy, placement patterns — only if free public unit_days data exists.

### Current state

**unit_days has 0 rows.** The table exists but requires paid (サイトセブン) or field-observed data. No free public unit daily data has been identified for any of the 66 halls.

### Decision

**Do not implement Q_unit computation or placement patterns in this phase.** Instead:

1. Create the DB tables (unit_outcomes, layouts) via migrate_db.py
2. Create stub interfaces in build_predictions.py
3. Add the vault exclusion test (US-04)
4. Document that activation requires free public unit daily data

If free public unit data is discovered for any hall during implementation, the full Q_unit pipeline can be activated for that hall only.

### Files to create

| File | Purpose |
|---|---|
| `tools/migrate_db.py` | Add unit_outcomes, layouts tables |
| `tests/test_unit_gate.py` | Verify no false unit predictions when data=0 |

### Acceptance tests

- US-01: No unit output for non-Top5/non-organic machines ✓
- US-04: Vault contains no unit_no, candidate_band, Qhat_unit ✓
- U-01: Q_unit code does not reference forbidden columns (static check) ✓

### Risks

- **LOW**: Schema-only addition, no behavioral change with 0 data.

---

## Phase E2E — End-to-End Validation

**Goal**: Full pipeline with real free public data.

### Steps

```bash
# 1. Migrate DB
python3 tools/migrate_db.py

# 2. Build predictions
python3 tools/build_predictions.py --source-mode free_public

# 3. Freeze run
python3 tools/freeze_run.py build/run_draft.json

# 4. Build site data (existing pipeline)
python3 tools/build_site_data.py --atlas-dir ../slot-atlas

# 5. Encrypt vault
SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs

# 6. Verify vault
node tools/decrypt_vault.mjs  # → verify rows, meta, free_source

# 7. Existing calendar smoke test
# Verify calendar renders, login works, existing hall predictions visible
```

### Acceptance tests

- E2E-01: Completes without authenticated sources ✓
- E2E-02: Generates hall/machine/tail predictions + chain patterns + frozen run ✓
- E2E-03: Works with real DB, not just fixtures ✓
- E2E-04: Existing calendar display preserved ✓
- E2E-05: Vault decrypts and passes schema check ✓

---

## Tenjikai UI Updates

### Phase 1 additions to detail panel

- Capability badge (FULL / SUMMARY / NONE with per-capability flags)
- Machine Top5 with event/organic distinction
- Previous selection / exclusion indicators
- Tail z-score tiles with shrinkage
- Confidence with breakdown factors
- Warnings list
- Prediction run ID and feature cutoff date
- Model version

### Phase 1.5 additions

- Chain correlation badge (4-type indicators)
- Chain explanation text

### Changes to app.js

The `renderFreeSource()` function (app.js lines 396-449) currently renders machine candidates, tail grid, and 15-pattern ledger. It needs to additionally consume:

- `predictions_v2` data for Top5 with v1.2 schema fields
- capability flags from hall_capabilities
- chain_pattern_results
- warnings per prediction
- run metadata (run_id, cutoff, model_version)

Strategy: Extend `free_source` payload in plain.json with new fields. Keep existing structure for backward compatibility.

### Changes to style.css

- Capability badge styles
- Warning display styles
- Chain pattern indicator styles
- Event/organic label distinction

---

## Run #1 Preparation

Per `docs/05_PREDICTION_RUN_001.md`:

- Target: 2026-07-20 BIG Dipper 戸越銀座 + 2026-07-21 マルハン池袋 SLOT BASE
- Prerequisites: Phase 0 complete (freeze_run.py works)
- Output: `predictions/frozen/manual_run_001.json` + `.sha256`
- 戸越銀座: FULL capability, machine candidates, rotation, tail z
- 池袋SB: SUMMARY capability, partial predictions, explicit capability gaps
- 末尾21 hypothesis: Must carry warnings ["tail_days 未接続", "日付こじつけ仮説", "無料検定なし"]

---

## Implementation Order

```
Phase 0  → freeze_run.py, migrate_db.py, canonical JSON, immutability
Phase 1A → raw_sources, machines, event_families, capabilities
Phase 1B → event_selected_label, organic gate
Phase 1C → machine features, score, Top5
Phase 1D → tail shrinkage z
  ↓ Run #1 freeze (2026-07-19)
Phase 1.5 → chain 4-type detectors
Phase 1.75 → unit stubs (schema only, no data)
E2E → full pipeline test
UI → Tenjikai display updates
```

Each phase is independently testable and leaves the system in a working state.

---

## File Change Summary

### New files (planned)

| File | Phase |
|---|---|
| tools/migrate_db.py | 0 |
| tools/build_predictions.py | 0 |
| tools/freeze_run.py | 0 |
| tools/evaluate_predictions.py | 0 |
| tools/normalize_sources.py | 1A |
| tools/build_event_families.py | 1A |
| tools/build_capabilities.py | 1A |
| tools/chain_detector.py | 1.5 |
| tests/test_prediction_run.py | 0 |
| tests/test_machine_labels.py | 1B |
| tests/test_machine_prediction.py | 1C |
| tests/test_tail_analysis.py | 1D |
| tests/test_chain_detector.py | 1.5 |
| tests/test_unit_gate.py | 1.75 |
| predictions/frozen/ | 0 |
| docs/current_state_inventory.md | 0 (investigation) |
| docs/schema_gap.md | 0 (investigation) |

### Modified files (planned)

| File | Phase | Change |
|---|---|---|
| tools/free_source_predictor.py | 1C, 1D | Integrate v1.2 scoring alongside existing |
| tools/build_site_data.py | 1C | Consume new prediction format |
| app.js | 1C, 1.5 | New UI sections for Top5, tail, chain, capabilities |
| style.css | 1C, 1.5 | New styles for added UI components |

### Unchanged files

| File | Reason |
|---|---|
| slot_atlas.py | External engine, continue as-is |
| atlas_plus.py | External engine, continue as-is |
| encrypt_vault.mjs | Vault format unchanged |
| decrypt_vault.mjs | Vault format unchanged |
| sw.js | Cache name auto-stamped by build_site_data.py |
| index.html | No structural changes needed |
| manifest.webmanifest | No changes |
