# Current bundled vault — 同期完了通知（2026-07-17）

**状態: デプロイ可能なbootstrap vaultへ同期済み。**

同梱の `data/vault.json` は、訂正版の凍結runと同期しています。

- 正run: `bootstrap_free_public_20260714_corrected`
- feature cutoff: `2026-07-14T23:59:59+09:00`
- target dates: `2026-07-20`, `2026-07-21`
- frozen payload SHA-256: `accc1150c0837c0797dc5faa22c8724665b2400794485c80f08edbe8185f8b92`
- encrypted vault SHA-256: `cb54ca4bbf2a9b52c9ba2e4f11805c6891c534fab59dad9ac6d687fa3f71880b`
- 旧run: `bootstrap_free_public_20260719`（superseded、原本を `archive/superseded/` に保存）

## 同梱Payload

- 既存カレンダー: 24,090行
- v1.2搭載店舗: 1店舗
- 機種予測: 10件
- 末尾予測: 10件
- 系列パターン: 0件

## 検証済み事項

1. corrected runはcanonical JSONで、ファイルSHAと記録SHAが一致
2. 旧run原本のSHAを維持したままsuperseded台帳へ登録
3. vaultを再暗号化し、独立復号に成功
4. vaultのrun ID、cutoff、payload SHAがcorrected runと一致
5. vault内の機種・末尾20件がcorrected runの予測20件と一致
6. `validate_release.py` によるPayload・frozen run検証に成功
7. 平文・資格情報は配布ZIPへ含めない

## 注意

本vaultは、元の運用 `slot_atlas.db` が同梱されていないため、既存vault内の無料集計を訂正版runへ同期したbootstrap版です。各予測にはその出所warningを保持しています。

外部の運用DBを接続した正式再計算は、次のコマンドで実施できます。

```bash
SITE_ID=... SITE_PASSWORD=... \
python3 tools/build_free_public_release.py \
  --atlas-dir /path/to/slot-atlas \
  --target-dates YYYY-MM-DD,YYYY-MM-DD
```
