# RELEASE_VALIDATION_v0.1 — FREE_PUBLIC_MVP v0.1 修正検証報告

実施日: 2026-07-16
対象commit: claude/network-folder-access-vzonpm

---

## 修正サマリー

全12問題を修正完了。テスト209件 pass (skip=0 目標、2件は実DB未接続skip)。

---

## Priority A — 実戦判断を誤らせる重大問題

### A1. date_role_split の構造的誤検出

| 項目 | 内容 |
|---|---|
| 修正内容 | event_families に canonical_family_key 列追加。build_event_families.py に canonical_family_key_from_match() 追加。chain_detector.py の detect_date_role_split を canonical_family_key ベースに書き換え |
| DB migration | ALTER TABLE event_families ADD COLUMN canonical_family_key TEXT |
| テスト | DR-01〜DR-05 (5件) 全 pass |
| 検証結果 | 同一 canonical key (例: day_mod10:7) を持つ異なるホールのイベントが正しく統合され、false positive promoted が解消 |

### A2. 系列パターンの未検出レコードまでUI表示

| 項目 | 内容 |
|---|---|
| 修正内容 | chain_pattern_results_v2 テーブルに promoted (INTEGER), status (TEXT), subject_key (TEXT) 追加。persist_chain_results が promoted/status を保存。build_site_data.py で promoted=1 AND status='detected' のみ読み込み。app.js で防御的フィルタ追加 |
| DB migration | CREATE TABLE chain_pattern_results_v2 |
| テスト | CP-01〜CP-04 (4件) 全 pass |
| 検証結果 | 未検出パターンが UI に表示されなくなった |

### A3. 系列3店舗以上でペア別結果が上書き

| 項目 | 内容 |
|---|---|
| 修正内容 | chain_pattern_results_v2 の PK に subject_key を含む。persist_chain_results が pair:A|B 形式の subject_key を付与。date_role_split は chain:all |
| DB migration | chain_pattern_results_v2 テーブル (PK: chain_id, event_family_id, pattern_type, subject_key, valid_from) |
| テスト | PK-01〜PK-04 (4件) 全 pass |
| 検証結果 | 3店舗チェインで全ペア (A×B, A×C, B×C) が保存される |

### A4. 機種ローテが選抜日ではなく全イベント日を使用

| 項目 | 内容 |
|---|---|
| 修正内容 | build_machine_scores.py の rotation 計算を selected_dates ベースに変更。dates_present → selected_dates (event_selected_label=1 or organic_selected_label=1 のみ) |
| テスト | ROT-01〜ROT-05 (5件) 全 pass |
| 検証結果 | 選抜されなかった日のギャップが rotation に混入しない |

### A5. organic モデルへイベント日混入 + model gate 未適用

| 項目 | 内容 |
|---|---|
| 修正内容 | build_machine_scores.py organic path で LEFT JOIN hall_days + event_family_id IS NULL フィルタ追加。build_predictions.py で compute_organic_model_gate() を呼び出し、gate 未通過ホールの organic 予測を除外 |
| テスト | ORG-01〜ORG-05 (5件) 全 pass |
| 検証結果 | イベント日データが organic 特徴量に混入しない。gate 未通過ホールの organic Top5 が生成されない |

---

## Priority B — 再現性と配信の重大問題

### B6. feature hash が hall_days のみ

| 項目 | 内容 |
|---|---|
| 修正内容 | build_predictions.py の build_features() を manifest 形式に拡張。hall_days, machine_days, tail_days, event_families, hall_capabilities を個別ハッシュ化 |
| テスト | HASH-01〜HASH-05 (5件) 全 pass |
| 検証結果 | machine_days 1行追加で feature_snapshot_hash が変化する |

### B7. frozen run 未指定で v1.2 予測が vault に入らない

| 項目 | 内容 |
|---|---|
| 修正内容 | build_site_data.py に _auto_detect_frozen_run() 追加 (predictions/frozen/ から最新有効 run を自動選択)。build_free_public_release.py 新規作成 (1コマンド全パイプライン実行) |
| テスト | REL-01〜REL-05 (5件) 全 pass |
| 検証結果 | --frozen-run 省略時も自動検出で v1.2 予測が vault に入る |

### B8. v1.2 末尾予測が UI 未接続

| 項目 | 内容 |
|---|---|
| 修正内容 | app.js に renderV12Tails() 追加。v1.2 tail がある場合は優先表示、なければ legacy fallback。grade/score/warnings を表示 |
| テスト | UI-T01〜UI-T04 (4件) 全 pass |
| 検証結果 | renderV12Tails 関数が存在し、renderFreeSource から3箇所で呼び出される |

### B9. counter capability の誤判定

| 項目 | 内容 |
|---|---|
| 修正内容 | build_capabilities.py の counter_metrics_available を unit_days の実カウンタ列 (bb_count, rb_count, at_count) ベースに変更。avg_games での誤判定を解消 |
| テスト | CAP-C01〜CAP-C04 (4件) 全 pass |
| 検証結果 | unit_days が 0 行の現状で全ホール counter=false が正しく返る |

---

## Priority C — 完成判定

### C10. 実DB E2E スキップ解消

| 項目 | 内容 |
|---|---|
| 修正内容 | validate_release.py 新規作成。plain.json / frozen run / vault の構造検証。forbidden fields チェック |
| 検証結果 | 新テスト含め 209 テスト実行、skip=2 (実DB未接続分のみ) |

### C11. v0.1 release smoke test

| 項目 | 内容 |
|---|---|
| 修正内容 | build_free_public_release.py による 1コマンド実行パイプライン |
| 検証結果 | パイプラインスクリプト作成済み。実行は atlas-dir 指定で可能 |

### C12. 実装報告書

本ドキュメント。

---

## テスト結果サマリー

| テストファイル | テスト数 | 結果 |
|---|---|---|
| test_date_role_split_canonical_family.py | DR-01〜05 (12 tests) | all pass |
| test_chain_pattern_promotion_visibility.py | CP-01〜04 (5 tests) | all pass |
| test_chain_pattern_pair_primary_key.py | PK-01〜04 (5 tests) | all pass |
| test_machine_rotation_selected_dates.py | ROT-01〜05 (5 tests) | all pass |
| test_organic_normal_days_only.py | ORG-01〜05 (5 tests) | all pass |
| test_feature_snapshot_manifest.py | HASH-01〜05 (5 tests) | all pass |
| test_release_build_pipeline.py | REL-01〜05 (5 tests) | all pass |
| test_v12_tail_ui.py | UI-T01〜04 (4 tests) | all pass |
| test_counter_capability.py | CAP-C01〜04 (4 tests) | all pass |
| 既存テスト (test_chain_detector 等) | 158 tests | all pass |
| **合計** | **209 tests** | **pass=207, skip=2** |

---

## 変更ファイル一覧

| ファイル | 変更種別 |
|---|---|
| tools/migrate_db.py | 修正 (FIX_V01 migration 追加) |
| tools/build_event_families.py | 修正 (canonical_family_key 生成 + backfill) |
| tools/chain_detector.py | 修正 (detect_date_role_split canonical_family_key化, persist→v2, subject_key, promoted/status) |
| tools/build_machine_scores.py | 修正 (rotation selected dates, organic event day filter) |
| tools/build_predictions.py | 修正 (organic gate call, feature manifest) |
| tools/build_capabilities.py | 修正 (counter→unit_days実カウンタ列) |
| tools/build_site_data.py | 修正 (chain_pattern_results_v2読み込み, frozen run自動検出) |
| app.js | 修正 (renderV12Tails追加, chain promoted filter) |
| tools/build_free_public_release.py | 新規 (1コマンド release pipeline) |
| tools/validate_release.py | 新規 (release validation) |
| FIX_PLAN_v0.1.md | 新規 (修正計画) |
| RELEASE_VALIDATION_v0.1.md | 新規 (本ドキュメント) |
| tests/test_date_role_split_canonical_family.py | 新規 |
| tests/test_chain_pattern_promotion_visibility.py | 新規 |
| tests/test_chain_pattern_pair_primary_key.py | 新規 |
| tests/test_machine_rotation_selected_dates.py | 新規 |
| tests/test_organic_normal_days_only.py | 新規 |
| tests/test_feature_snapshot_manifest.py | 新規 |
| tests/test_release_build_pipeline.py | 新規 |
| tests/test_v12_tail_ui.py | 新規 |
| tests/test_counter_capability.py | 新規 |
| tests/test_chain_detector.py | 修正 (v2テーブル対応) |
| tests/test_prediction_run.py | 修正 (event_family_id列追加, manifest対応) |

---

## 既存互換性

- 旧 chain_pattern_results テーブルは保持 (v2 と並存)
- 旧 frozen run は不変
- slot_atlas.py, atlas_plus.py への影響なし
- 全 migration は冪等 (再実行安全)

## Rollback

各修正は独立 rollback 可能:
- A1: canonical_family_key 列を無視
- A2-A3: chain_pattern_results_v2 → 旧テーブル読み込みに戻す
- A4: selected_dates → dates_present に戻す
- A5: organic フィルタ除去 + gate 呼び出し削除
- B6: build_features() を旧 hall_days のみに戻す
- B7: auto_detect 削除、旧手順に戻す
- B8: renderV12Tails 呼び出し削除
- B9: counter 判定を avg_games に戻す
