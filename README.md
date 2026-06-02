# mac-heatwatch-discord

Mac mini のチップ温度を `macmon` で30分ごとに確認し、想定外の発熱上昇を Discord に日本語で通知する叩き台です。

Codex heartbeat ではなく、macOS の `launchd` LaunchAgent として動かします。

## 動作

- `macmon pipe --soc-info` で CPU/GPU 平均温度を取得
- CPU/GPU の高い方を現在の温度帯として判定
- `60℃` 未満に戻ったら通知状態をリセット
- 通知済みの温度帯から `60℃` 未満に戻った場合は、正常化したことも通知
- 同じ温度帯に居続けている間は再通知しない
- より高い温度帯に上がったら再通知
- `danger`（90℃以上）だけは例外として、danger帯にいる間は毎回通知
- 通知時だけ `ps` で CPU/メモリ上位プロセスを確認し、原因候補として添える

## 温度帯

| band | 範囲 |
| --- | --- |
| normal | 60℃未満 |
| watch | 60.0〜69.9℃ |
| elevated | 70.0〜79.9℃ |
| hot | 80.0〜89.9℃ |
| danger | 90.0℃以上 |

## 設定

設定ファイルは `~/Library/Application Support/mac-heat-watch/config.json` に置きます。

```bash
mkdir -p "$HOME/Library/Application Support/mac-heat-watch"
cp ./config.example.json "$HOME/Library/Application Support/mac-heat-watch/config.json"
```

主な設定:

- `interval_seconds`: 監視間隔。デフォルトは `1800` 秒。変更後は `./scripts/install_launch_agent.sh` を再実行してください
- `notify_on_recovery`: `true` なら、通知済み温度帯から normal に戻った時も通知
- `repeat_highest_band`: `true` なら、一番上の温度帯にいる間は毎回通知
- `temperature_bands`: 温度帯。最初の `name` は `normal`、最後の `max_c` は `null` にしてください
- `keychain_service`: webhook URL を読む Keychain service 名

例:

```json
{
  "interval_seconds": 1800,
  "notify_on_recovery": true,
  "repeat_highest_band": true,
  "temperature_bands": [
    { "name": "normal", "label_ja": "通常", "min_c": 0, "max_c": 60 },
    { "name": "watch", "label_ja": "注意", "min_c": 60, "max_c": 70 },
    { "name": "elevated", "label_ja": "警戒", "min_c": 70, "max_c": 80 },
    { "name": "hot", "label_ja": "高温", "min_c": 80, "max_c": 90 },
    { "name": "danger", "label_ja": "危険", "min_c": 90, "max_c": null }
  ]
}
```

## 前提

```bash
brew install macmon
```

Discord の warning webhook URL は、環境変数をそのまま launchd に渡すより Keychain に保存する運用を想定しています。

```bash
export DISCORD_WARNING_WEBHOOK_URL='<your Discord warning webhook URL>'
./scripts/save_webhook_to_keychain.sh
unset DISCORD_WARNING_WEBHOOK_URL
```

URLは `DISCORD_WARNING_WEBHOOK_URL` という Keychain service 名で保存されます。スクリプトは環境変数、Keychain、設定ファイルの順で利用できます。

## 手動確認

```bash
/usr/bin/python3 ./src/mac_heat_watch.py --print-status
```

Discord へ送らず、送信予定の payload だけ確認する場合:

```bash
/usr/bin/python3 ./src/mac_heat_watch.py --dry-run --print-status
```

現在の温度が `60℃` 未満で、直前に通知済みの温度帯がなければ payload は出ません。

## LaunchAgent としてインストール

```bash
./scripts/install_launch_agent.sh
```

アンインストール:

```bash
./scripts/uninstall_launch_agent.sh
```

## License

MIT

## ログと状態

- ログ: `~/Library/Logs/mac-heat-watch/`
- 状態: `~/Library/Application Support/mac-heat-watch/state.json`
- 任意設定: `~/Library/Application Support/mac-heat-watch/config.json`

`discord_warning_webhook_url` を `config.json` に直接置くこともできますが、秘密情報なので Keychain 推奨です。
