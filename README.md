# Slot Atlas — 狙い目カレンダー（仲間内限定）

`slot-atlas`（パチスロホールの日次データ分析）の予測結果を、**リンクを知っている人だけ**が
スマホ・PCのブラウザで見られるカレンダーにしたものです。GitHub Pages で配信します。

- 検索エンジンには登録しない設定（`robots.txt` の全体 Disallow + `<meta name="robots" content="noindex">`）
- ログイン・パスワードの類は**ありません**。URLを知っていれば誰でも開けます。積極的に共有・拡散しないでください
- 一度開けばオフラインでも起動（PWA。ホーム画面に追加すると全画面アプリ風に使えます）

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

## ファイル構成

| パス | 役割 |
|---|---|
| `index.html` | カレンダー本体（単一ページ） |
| `data/candidates.json` | 365日×全店の予測候補（`tools/build_site_data.py` が生成） |
| `data/meta.json` | モデルバージョン・対象期間などのメタ情報 |
| `slot-atlas/` | 元データと分析コード一式（`slot_atlas.py`、`seed/`、`exports/`、`tests/` など） |
| `tools/build_site_data.py` | `slot-atlas/exports/forecast_candidates_365.csv` から `data/*.json` を再生成 |
| `manifest.webmanifest` / `sw.js` / `icons/` | PWA設定・オフラインキャッシュ |
| `robots.txt` | 検索エンジンのクロールを全面拒否 |
| `.github/workflows/pages.yml` | GitHub Pages への自動デプロイ |

## データを更新する

1. `slot-atlas/` 側でデータ収集・再計算を行い、`slot-atlas/exports/forecast_candidates_365.csv` を再生成する
   （`slot-atlas/README.md` の「実行」セクション参照）
2. サイト用データを再生成:
   ```bash
   python3 tools/build_site_data.py
   ```
3. `main` に反映すると自動で再デプロイされます

## 公開範囲についての注意

- リポジトリ自体は現状パブリックです。`robots.txt`／`noindex` は検索エンジン向けの対策であり、
  GitHub 上でリポジトリを直接閲覧されることまでは防げません
- 本格的にアクセス制限（パスワード・招待制など）をしたい場合は、Cloudflare Access や
  Basic 認証を挟めるホスティングへの切り替えが必要です。現状はあくまで「積極的には公開しない」
  レベルの運用です

## 免責

掲載内容は公開情報をもとにした統計的な傾向整理であり、実際の出玉・結果を保証するものではありません。
閲覧目的の参考情報として扱ってください。
