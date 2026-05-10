# Live2D Booth Preview Maker

BOOTH販売用Live2D動作プレビュー動画のための、ローカルWEB GUI型テンプレートレンダラーです。

## セットアップ

Windows:

```bat
setup_windows.bat
```

通常起動:

```bat
run_app.bat
```

ブラウザで `http://127.0.0.1:7860` が開きます。

## 素材

- 背景: `templates/background/` に PNG / JPG / JPEG を配置
- BGM: `templates/bgm/` に MP3 / WAV / M4A を配置
- Live2D透過動画: `projects/product_001/` などに WebM / MOV / MP4 / MKV を配置

GUIの「外部ファイル参照」は、PC上のファイルを保存せずに一時プレビューへ反映します。
ファイルを選んだ後に保存名を入力し、「アプリ内ライブラリへ登録」を押すと、素材ライブラリ用ディレクトリに保存され、以後プルダウンから選べます。動画作成に使う素材はライブラリ登録済みである必要があります。

## プリセット

設定は JSON として保存します。標準保存先は `projects/product_001/preset.json` です。

## 管理画面

- 編集: 素材選択、プレビュー、設定、動画作成
- 素材管理: 登録済みの背景 / 動画 / BGM の確認、編集への適用、削除
- 出力確認: `projects/*/output/` にある MP4 / MKV の一覧、再生確認、ダウンロード、削除

## 動画出力

「動画作成」ボタンで、現在の設定に従って MP4 を出力します。出力先を `.mkv` にした場合は MKV として出力します。FFmpeg は `imageio-ffmpeg` 経由で取得した実行ファイルを使用します。
