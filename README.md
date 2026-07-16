# Slot Atlas — 狙い目カレンダー（仲間内限定・ログイン制）

`slot-atlas`（パチスロホールの日次データ分析）の予測結果を、**ID・パスワードを知っている人だけ**が
スマホ・PCのブラウザで見られるカレンダーにしたものです。GitHub Pages で配信します。

- ログイン必須。データ本体（`data/vault.json`）はID・パスワードから作った鍵でしか復号できない暗号化済みファイルとして配信します
- 検索エンジンには登録しない設定（`robots.txt` の全体Disallow + `<meta name="robots" content="noindex">`）
- このリポジトリには元の生データ（ホール名・実測値などのCSV/JSON）を一切含めていません。含まれるのは暗号化済みデータとコードだけです
- 一度ログインすればオフラインでも起動（PWA。ホーム画面に追加すると全画面アプリ風に使えます）

---

## 公開URL

GitHub Pages を有効化すると次のURLで配信されます（正確な値は Settings → Pages で確認）。

```
https://riecle.github.io/Tenjikai/
```

### 有効化手順（初回だけ）
1. このブランチを `main` に反映する（Pull Request を作ってマージ）
2. GitHub の **Settings → Pages** → **Build and deployment → Source** を **「GitHub Actions」** に設定
3. 数十秒〜1分ほどで公開されます

> すぐ試したい場合：Actions タブ → 「Deploy to GitHub Pages」→ **Run workflow** で、
> このブランチを選んで手動実行すると、`main` へマージしなくてもプレビューできます。

---

## ログインについて

- ID・パスワードはこのリポジトリのどこにも書かれていません（コード上は一切保持しません）
- ログイン成功時のみ、`data/vault.json` の中身を復号して表示します。パスワードが違えば復号自体が失敗し、
  データは一切見えません（画面にログインフォームを出しているだけ、ではありません）
- 連続でログインに失敗すると、待機時間が指数的に増える簡易ロックアウトがかかります（ブラウザ内蔵の対策）
- **既知の限界**: これは静的サイト（サーバーなし）のため、暗号化ファイル自体を直接入手して
  オフラインで総当たりを試みるような本格的な攻撃までは防げません。特に短い数字だけのパスワードは
  弱いため、心配な場合はより長い・複雑なパスワードに変更することを推奨します（変更方法は下記）

## ファイル構成

| パス | 役割 |
|---|---|
| `index.html` | ページの骨格（CSP設定・ログイン画面のマウント先） |
| `app.js` | ログイン・復号・カレンダー描画のロジック |
| `style.css` | スタイル |
| `data/vault.json` | 暗号化済みの365日×全店データ（PBKDF2 + AES-GCM） |
| `manifest.webmanifest` / `sw.js` / `icons/` | PWA設定・オフラインキャッシュ |
| `robots.txt` | 検索エンジンのクロールを全面拒否 |
| `tools/build_site_data.py` | 手元の slot-atlas からカレンダー＋無料ソース配置予測の平文JSONを生成 |
| `tools/free_source_predictor.py` | machine_days / tail_days / unit_days等からFULL・SUMMARY・NONEと15型台帳を生成 |
| `README_無料ソース予測.md` | 入力ファイル・列契約・判定仕様 |
| `tests/test_free_source_predictor.py` | FULL / SUMMARY / NONE、ローテ、末尾zの回帰テスト |
| `CHANGELOG_2026-07-16.md` | 今回の変更点と注意事項 |
| `tools/encrypt_vault.mjs` | 平文JSONをID・パスワードで暗号化して `data/vault.json` を生成 |
| `.github/workflows/pages.yml` | GitHub Pages への自動デプロイ |

## データを更新する

元データ（`slot-atlas/` プロジェクト一式）はこのリポジトリには置いていません。お渡しした
「Slot Atlas サイト更新キット」（zip）をお手元で展開し、同梱の `README_更新手順.md` の手順で
`data/vault.json` を作り直して、それだけをこのリポジトリにコミット・pushしてください。

概要:
```bash
python3 tools/build_site_data.py --atlas-dir /path/to/slot-atlas
SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs
git add data/vault.json
git commit -m "update data"
```

現在の暗号化vaultに入っている365日予測を保持したまま、機種・末尾・台番分析だけを追加する場合:

```bash
SITE_ID=... SITE_PASSWORD=... node tools/decrypt_vault.mjs
python3 tools/build_site_data.py \
  --atlas-dir /path/to/slot-atlas \
  --base-plain build/plain.json
SITE_ID=... SITE_PASSWORD=... node tools/encrypt_vault.mjs
```

`build/plain.json` は平文なのでgitには追加しないでください（`.gitignore` 済み）。共有PCでは再暗号化後に削除してください。

`machine_days`、`tail_days`、`machine_scores`、`position_signals`、`unit_days` がSlot Atlas側にあれば自動検出し、機種・末尾・配置型を同じ暗号化payloadへ追加します。詳しい列契約は [`README_無料ソース予測.md`](README_無料ソース予測.md) を参照してください。

## パスワードを変更する

`tools/encrypt_vault.mjs` を新しい `SITE_PASSWORD` で実行し直し、`data/vault.json` をコミットするだけです。
コードの変更は不要です。通常のデータ更新では既存のKDF saltを再利用するため、ID・パスワードが同じなら端末のログイン状態を維持できます。salt自体も更新したい場合は `ROTATE_KDF_SALT=1` を付けて実行してください。

## 公開範囲についての注意

- リポジトリ自体は現状パブリックです。ただし `slot-atlas/` の生データは含めていないため、
  GitHubを直接見に行っても暗号化済みファイルとコードしか見えません
- `robots.txt`／`noindex`は検索エンジン向けの対策です
- 本格的なアクセス制限（IPブロック・多要素認証など）が必要な場合は、Cloudflare Access のような
  サーバー側の仕組みへの切り替えが必要です

## 免責

掲載内容は公開情報をもとにした統計的な傾向整理であり、実際の出玉・結果を保証するものではありません。
閲覧目的の参考情報として扱ってください。
