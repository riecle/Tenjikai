# FREE_PUBLIC_MVP v0.1.2 Final Validation Report

## Release status

- **Code and deterministic release pipeline:** PASS
- **Bundled bootstrap vault synchronization:** PASS
- **Frozen-run immutability and correction lineage:** PASS
- **ZIP safety / plaintext exclusion:** PASS
- **Full rerun against external operational `slot_atlas.db`:** NOT EXECUTED（DBは同梱されていない）

## Test result

```text
Ran 238 tests
Passed: 219
Failed: 0
Skipped: 19
```

19件は外部の運用 `slot_atlas.db` を必要とするE2Eです。コード単体・合成データ・Payload・凍結・cutoff・ZIP安全性のテストは成功しています。

## Corrected frozen run

```text
prediction_run_id: bootstrap_free_public_20260714_corrected
built_at: 2026-07-16T22:55:32+00:00
feature_cutoff_at: 2026-07-14T23:59:59+09:00
target_dates: 2026-07-20, 2026-07-21
payload SHA-256: accc1150c0837c0797dc5faa22c8724665b2400794485c80f08edbe8185f8b92
```

- canonical JSONとファイル内容が完全一致
- SHA-256ファイルと実ファイルSHAが一致
- `feature_cutoff_at <= built_at` を満たす
- 旧run `bootstrap_free_public_20260719` は上書きせず、原本をarchiveへ保存してsuperseded化

## Bundled encrypted vault

```text
calendar rows: 24,090
prediction_run_id: bootstrap_free_public_20260714_corrected
feature_cutoff_at: 2026-07-14T23:59:59+09:00
v1.2 halls: 1
machine predictions: 10
tail predictions: 10
chain patterns: 0
vault SHA-256: cb54ca4bbf2a9b52c9ba2e4f11805c6891c534fab59dad9ac6d687fa3f71880b
```

検証結果:

- AES-GCM暗号化self-check: PASS
- 独立復号: PASS
- Payload schema: PASS
- corrected runとのrun ID一致: PASS
- corrected runとのcutoff一致: PASS
- corrected runとのpayload SHA一致: PASS
- 機種・末尾20件の内容一致: PASS
- 旧カレンダー24,090行の維持: PASS
- 平文・認証情報の非同梱: PASS

## Release validation command

```bash
python3 tools/validate_release.py \
  --plain build/plain.json \
  --frozen-run predictions/frozen/bootstrap_free_public_20260714_corrected.json \
  --cutoff 2026-07-14T23:59:59+09:00 \
  --skip-test-suite
```

Result: `VALIDATION PASSED`

## Operational DB limitation

元の運用DBが配布物にないため、実DB依存の19件は未実行です。本配布物は、既存無料集計から作成したbootstrap予測を正しい凍結runへ同期したものです。外部DBを接続する際は、`--fail-on-skip` を使用して正式E2Eを実行してください。

## Final decision

**PASS — bootstrap配布版としてデプロイ可能。**

外部運用DBからの全面再計算については、DB接続後の正式E2Eを別途必要とします。
