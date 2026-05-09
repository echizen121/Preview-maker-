# SECURITY.md

## 1. 目的
本ドキュメントは、ローカル実行主体の開発において、secrets の混入・追跡・漏えいを防止するための制約と確認手順を定めるものである。

secrets には、API key、access token、refresh token、SSH private key、認証情報を含む接続文字列、`.env` の実値、その他漏えい時に第三者利用や不正接続を招く情報を含む。

## 2. 最優先原則
- secrets はコード・Markdown・ログ・プロンプトに直書きしない
- secrets は Git の追跡対象に入れない
- secrets は環境変数またはローカル専用ファイルでのみ管理する
- 漏えいの疑いが出た secrets は、削除だけで済ませず、失効・再発行を行う
- 「いまはローカルだけだから大丈夫」という判断で例外を作らない

## 3. 禁止事項
- secrets をコード、Markdown、コメント、サンプル、ログへ直書きしない
- `.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa`, `id_ed25519` などを Git 追跡対象に入れない
- AI への質問文、デバッグログ、貼り付け用メモに secrets を含めない
- SSH private key をプロジェクト配下に置かない

## 4. 許可される保管場所
- 環境変数
- ローカル専用の `.env` ファイル
- OS / ツール標準の秘密情報保管領域
- `~/.ssh/` 配下の SSH 鍵

## 5. 文書・コードに記載する値の扱い
文書やコード例には実値を書かず、必ずプレースホルダを使う。

使用例:
- `YOUR_API_KEY_HERE`
- `${OPENAI_API_KEY}`
- `<SSH_PRIVATE_KEY>`
- `https://example.com`
- `token=REDACTED`

## 6. step 終了時の確認
各 step 終了時に以下を確認する。

1. 追加・変更したファイルに secrets が含まれていないか
2. Markdown、コメント、設定ファイル、ログにトークン断片が混入していないか
3. `.env` や鍵ファイルが新たに生成・移動・コピーされていないか
4. 不自然な長い文字列が説明不能な状態で残っていないか

## 7. コミット前の必須確認
コミット前に必ず以下を実施する。

1. `git status` を確認する
2. `git diff --cached` を確認する
3. secrets に該当するファイルや文字列が staged に含まれていないことを確認する
4. `.gitignore` が想定どおり機能していることを確認する
5. 必要に応じて secrets scanner を実行する

## 8. 検出対象の具体例
以下の文字列・形式を見つけた場合、secrets 混入を疑う。

- `sk-`
- `AIza`
- `AKIA`
- `ghp_`
- `github_pat_`
- `xoxb-`
- `xoxp-`
- `Bearer `
- `BEGIN PRIVATE KEY`
- `BEGIN OPENSSH PRIVATE KEY`
- `ssh-rsa`
- `password=`
- `token=`
- `api_key=`
- `secret=`
- 認証情報付き URL

検出された文字列が本当に secrets か不明な場合でも、まず疑義ありとして扱う。

## 9. 異常時の処理
secrets 混入または漏えいの疑いを検出した場合、該当 secrets を除去し、すでに共有・commit 済みであれば履歴汚染を前提として失効または再発行する。
