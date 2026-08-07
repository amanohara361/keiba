このフォルダは jra_bias.py の ingest コマンド用の受け皿です。

Claudeの実行環境（サンドボックス）は外部通信がホワイトリスト方式で制限されており、
db.netkeiba.com に到達できないため `jra_bias.py fetch` が使えません。
そこでClaudeが web_fetch で取得したスポーツナビの結果ページを、ここに

    YYYYMMDD_{race_id}.txt      例) 20260801_202601010311.txt

というファイル名で保存し、`jra_bias.py ingest YYYYMMDD 場名` で
data_jra\ の JSON（fetchが作るものと同一形式）に変換します。

Windows側で作業する場合は バイアス確認.bat（fetch経由）がそのまま使えるので、
このフォルダを気にする必要はありません。中身は消しても問題ありません。
