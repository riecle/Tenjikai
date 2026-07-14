PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS halls (
    hall_id TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    forecast_enabled INTEGER NOT NULL DEFAULT 1 CHECK (forecast_enabled IN (0, 1)),
    forecast_block_reason TEXT,
    slot_count INTEGER,
    exchange_label TEXT,
    decision_floor REAL NOT NULL DEFAULT 0,
    travel_origin TEXT,
    travel_minutes INTEGER,
    travel_status TEXT,
    travel_source_url TEXT,
    grand_open_date TEXT,
    baseline_mean REAL,
    baseline_n INTEGER,
    data_through TEXT,
    source_kind TEXT NOT NULL,
    source_url TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS source_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    hall_id TEXT REFERENCES halls(hall_id),
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    http_status INTEGER,
    content_sha256 TEXT,
    payload_path TEXT,
    parse_status TEXT NOT NULL,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS hall_days (
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    result_date TEXT NOT NULL,
    avg_diff REAL,
    total_diff REAL,
    avg_games REAL,
    machine_win_rate REAL,
    winning_units INTEGER,
    total_units INTEGER,
    source_name TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    snapshot_id INTEGER REFERENCES source_snapshots(snapshot_id),
    PRIMARY KEY (hall_id, result_date, source_name)
);

CREATE TABLE IF NOT EXISTS machine_days (
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    result_date TEXT NOT NULL,
    machine_key TEXT NOT NULL,
    machine_name TEXT NOT NULL,
    units INTEGER,
    avg_diff REAL,
    avg_games REAL,
    winning_units INTEGER,
    total_units INTEGER,
    selected_flag INTEGER CHECK (selected_flag IN (0, 1)),
    source_name TEXT NOT NULL,
    snapshot_id INTEGER REFERENCES source_snapshots(snapshot_id),
    PRIMARY KEY (hall_id, result_date, machine_key, source_name)
);

CREATE TABLE IF NOT EXISTS tail_days (
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    result_date TEXT NOT NULL,
    tail_key TEXT NOT NULL,
    avg_diff REAL,
    avg_games REAL,
    winning_units INTEGER,
    total_units INTEGER,
    source_name TEXT NOT NULL,
    snapshot_id INTEGER REFERENCES source_snapshots(snapshot_id),
    PRIMARY KEY (hall_id, result_date, tail_key, source_name)
);

CREATE TABLE IF NOT EXISTS machine_scores (
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    as_of_date TEXT NOT NULL,
    machine_key TEXT NOT NULL,
    machine_name TEXT NOT NULL,
    units INTEGER,
    baseline_days INTEGER NOT NULL,
    baseline_avg_diff REAL,
    special_selected_n INTEGER NOT NULL DEFAULT 0,
    momentum_selected_n INTEGER NOT NULL DEFAULT 0,
    composite_score REAL,
    type_label TEXT NOT NULL,
    source_name TEXT NOT NULL,
    notes TEXT,
    PRIMARY KEY (hall_id, as_of_date, machine_key, source_name)
);

CREATE TABLE IF NOT EXISTS position_signals (
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    result_date TEXT NOT NULL,
    event_name TEXT NOT NULL,
    machine_key TEXT NOT NULL,
    machine_name TEXT NOT NULL,
    unit_numbers_json TEXT NOT NULL,
    unit_count INTEGER NOT NULL,
    winning_units INTEGER,
    avg_diff REAL,
    avg_games REAL,
    rate_scope TEXT NOT NULL DEFAULT 'unknown',
    source_name TEXT NOT NULL,
    notes TEXT,
    PRIMARY KEY (hall_id, result_date, event_name, machine_key, source_name)
);

CREATE TABLE IF NOT EXISTS calendar_flags (
    flag_date TEXT NOT NULL,
    hall_id TEXT NOT NULL DEFAULT '*',
    flag_type TEXT NOT NULL,
    flag_name TEXT NOT NULL,
    pre_registered INTEGER NOT NULL DEFAULT 1 CHECK (pre_registered IN (0, 1)),
    source_url TEXT,
    PRIMARY KEY (flag_date, hall_id, flag_type, flag_name)
);

CREATE TABLE IF NOT EXISTS evidence_rules (
    rule_id TEXT PRIMARY KEY,
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    label TEXT NOT NULL,
    priority INTEGER NOT NULL,
    match_json TEXT NOT NULL,
    mean_diff REAL NOT NULL,
    sample_n INTEGER,
    positive_rate REAL,
    valid_from TEXT,
    valid_to TEXT,
    data_through TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_url TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS model_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    model_version TEXT NOT NULL,
    target_start TEXT NOT NULL,
    target_end TEXT NOT NULL,
    data_cutoff TEXT NOT NULL,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS predictions (
    run_id INTEGER NOT NULL REFERENCES model_runs(run_id),
    target_date TEXT NOT NULL,
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    rule_id TEXT REFERENCES evidence_rules(rule_id),
    predicted_mean REAL NOT NULL,
    adjusted_edge REAL NOT NULL,
    utility_edge REAL NOT NULL DEFAULT 0,
    travel_minutes INTEGER,
    travel_penalty REAL NOT NULL DEFAULT 0,
    confidence REAL NOT NULL,
    rank TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    PRIMARY KEY (run_id, target_date, hall_id)
);

CREATE TABLE IF NOT EXISTS validation_log (
    validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES model_runs(run_id),
    target_date TEXT NOT NULL,
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    claim TEXT NOT NULL,
    threshold_json TEXT NOT NULL,
    observed_json TEXT,
    verdict TEXT CHECK (verdict IN ('pending', 'kill', 'confirm', 'reframe')),
    evaluated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hall_days_date ON hall_days(result_date);
CREATE INDEX IF NOT EXISTS idx_machine_days_date ON machine_days(result_date);
CREATE INDEX IF NOT EXISTS idx_machine_scores_hall ON machine_scores(hall_id, as_of_date);
CREATE INDEX IF NOT EXISTS idx_position_signals_hall ON position_signals(hall_id, result_date);
CREATE INDEX IF NOT EXISTS idx_predictions_date ON predictions(target_date);
CREATE INDEX IF NOT EXISTS idx_rules_hall ON evidence_rules(hall_id, status);

-- v0.7: per-unit daily results (台番検定の受け皿). Populated by
-- `atlas_plus.py import-unit-days` from paid daily sources or field notes.
CREATE TABLE IF NOT EXISTS unit_days (
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    result_date TEXT NOT NULL,
    unit_no INTEGER NOT NULL,
    machine_name TEXT NOT NULL,
    diff REAL NOT NULL,
    games REAL,
    source_name TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (hall_id, result_date, unit_no, source_name)
);

-- v0.7: CUSUM-detected operating-regime boundaries per hall.
CREATE TABLE IF NOT EXISTS regime_changes (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    change_date TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('up', 'down')),
    cusum_stat REAL NOT NULL,
    window_mean_before REAL,
    window_mean_after REAL,
    detected_at TEXT NOT NULL,
    params_json TEXT,
    UNIQUE (hall_id, change_date, direction)
);

-- v0.7: hall habit vectors (店の運用癖の定量化).
CREATE TABLE IF NOT EXISTS habit_vectors (
    hall_id TEXT NOT NULL REFERENCES halls(hall_id),
    as_of_date TEXT NOT NULL,
    zero_sum_r REAL,
    prev_day_squeeze REAL,
    event_compliance REAL,
    weekend_penalty REAL,
    burst_recovery_lag_days REAL,
    n_days INTEGER NOT NULL,
    notes TEXT,
    PRIMARY KEY (hall_id, as_of_date)
);
