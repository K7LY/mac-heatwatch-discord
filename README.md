# mac-heatwatch-discord

Periodically checks Mac mini chip temperatures with `macmon` and sends Japanese Discord notifications when unexpected heat rises are detected.

Mac mini のチップ温度を `macmon` で定期的に確認し、想定外の発熱上昇を Discord に日本語で通知します。

## Behavior

- Reads average CPU/GPU temperatures with `macmon pipe --soc-info`
- Uses the higher CPU/GPU temperature as the current heat level
- Resets notification state when the temperature returns below `60 C`
- Sends a recovery notification when a previously notified band returns to normal
- Suppresses repeated notifications while staying in the same band
- Sends another notification when the temperature moves into a higher band
- Keeps checking at the active interval while the temperature is above the configured active band
- Repeats highest-band notifications at a configurable interval, `danger` every 5 minutes by default
- Includes likely load-causing processes from `ps`, with CPU normalized to whole-system percentage and without process arguments

## 動作

- `macmon pipe --soc-info` で CPU/GPU 平均温度を取得
- CPU/GPU の高い方を現在の温度帯として判定
- `60℃` 未満に戻ったら通知状態をリセット
- 通知済みの温度帯から normal に戻った場合は、正常化したことも通知
- 同じ温度帯に居続けている間は再通知しない
- より高い温度帯に上がったら再通知
- 設定したactive温度帯以上では、active間隔で監視し続ける
- 一番上の温度帯、デフォルトでは `danger`、の連続通知は設定した間隔で送る
- 通知時だけ `ps` で原因候補プロセスを確認し、CPUは全体比に正規化し、プロセス引数は含めない

## Temperature Bands

| band | range |
| --- | --- |
| normal | below 60 C |
| watch | 60.0-69.9 C |
| elevated | 70.0-79.9 C |
| hot | 80.0-89.9 C |
| danger | 90.0 C or higher |

## 温度帯

| band | 範囲 |
| --- | --- |
| normal | 60℃未満 |
| watch | 60.0〜69.9℃ |
| elevated | 70.0〜79.9℃ |
| hot | 80.0〜89.9℃ |
| danger | 90.0℃以上 |

## Requirements

```bash
brew install macmon
```

Discord warning webhook URLs should be stored in Keychain.

```bash
export DISCORD_WARNING_WEBHOOK_URL='<your Discord warning webhook URL>'
./scripts/save_webhook_to_keychain.sh
unset DISCORD_WARNING_WEBHOOK_URL
```

The URL is stored under the `DISCORD_WARNING_WEBHOOK_URL` Keychain service name. The app reads the webhook URL from environment variables, Keychain, or config, in that order.

## 前提

```bash
brew install macmon
```

Discord の warning webhook URL は Keychain に保存する運用を推奨します。

```bash
export DISCORD_WARNING_WEBHOOK_URL='<your Discord warning webhook URL>'
./scripts/save_webhook_to_keychain.sh
unset DISCORD_WARNING_WEBHOOK_URL
```

URLは `DISCORD_WARNING_WEBHOOK_URL` という Keychain service 名で保存されます。アプリは環境変数、Keychain、設定ファイルの順で webhook URL を読み込みます。

## Configuration

Put the config file at `~/Library/Application Support/mac-heat-watch/config.json`.

```bash
mkdir -p "$HOME/Library/Application Support/mac-heat-watch"
cp ./config.example.json "$HOME/Library/Application Support/mac-heat-watch/config.json"
```

Main options:

- `interval_seconds`: normal monitoring interval. Default is `1800` seconds. Run `./scripts/install_launch_agent.sh` again after changing it
- `active_interval_seconds`: monitoring interval while at or above `active_from_band`
- `active_from_band`: band that starts active monitoring. Default behavior uses the first non-normal band
- `notify_on_recovery`: when `true`, notify when a notified band returns to normal
- `repeat_highest_band`: when `true`, allow repeated notifications while in the highest band
- `highest_band_repeat_interval_seconds`: minimum interval between repeated highest-band notifications. Use `0` to notify every active check
- `temperature_bands`: configurable temperature bands. The first band must be `normal`, and the final band must have `"max_c": null`
- `keychain_service`: Keychain service name used to read the webhook URL

Example:

```json
{
  "interval_seconds": 1800,
  "active_interval_seconds": 30,
  "active_from_band": "watch",
  "notify_on_recovery": true,
  "repeat_highest_band": true,
  "highest_band_repeat_interval_seconds": 300,
  "temperature_bands": [
    { "name": "normal", "label_ja": "通常", "min_c": 0, "max_c": 60 },
    { "name": "watch", "label_ja": "注意", "min_c": 60, "max_c": 70 },
    { "name": "elevated", "label_ja": "警戒", "min_c": 70, "max_c": 80 },
    { "name": "hot", "label_ja": "高温", "min_c": 80, "max_c": 90 },
    { "name": "danger", "label_ja": "危険", "min_c": 90, "max_c": null }
  ]
}
```

## 設定

設定ファイルは `~/Library/Application Support/mac-heat-watch/config.json` に置きます。

```bash
mkdir -p "$HOME/Library/Application Support/mac-heat-watch"
cp ./config.example.json "$HOME/Library/Application Support/mac-heat-watch/config.json"
```

主な設定:

- `interval_seconds`: 通常時の監視間隔。デフォルトは `1800` 秒。変更後は `./scripts/install_launch_agent.sh` を再実行してください
- `active_interval_seconds`: `active_from_band` 以上にいる間の監視間隔
- `active_from_band`: active監視を開始する温度帯。デフォルト動作では最初の非normal帯を使います
- `notify_on_recovery`: `true` なら、通知済み温度帯から normal に戻った時も通知
- `repeat_highest_band`: `true` なら、一番上の温度帯にいる間の連続通知を許可
- `highest_band_repeat_interval_seconds`: 一番上の温度帯での連続通知の最短間隔。`0` ならactive監視ごとに通知
- `temperature_bands`: 温度帯。最初の `name` は `normal`、最後の `max_c` は `null` にしてください
- `keychain_service`: webhook URL を読む Keychain service 名

例:

```json
{
  "interval_seconds": 1800,
  "active_interval_seconds": 30,
  "active_from_band": "watch",
  "notify_on_recovery": true,
  "repeat_highest_band": true,
  "highest_band_repeat_interval_seconds": 300,
  "temperature_bands": [
    { "name": "normal", "label_ja": "通常", "min_c": 0, "max_c": 60 },
    { "name": "watch", "label_ja": "注意", "min_c": 60, "max_c": 70 },
    { "name": "elevated", "label_ja": "警戒", "min_c": 70, "max_c": 80 },
    { "name": "hot", "label_ja": "高温", "min_c": 80, "max_c": 90 },
    { "name": "danger", "label_ja": "危険", "min_c": 90, "max_c": null }
  ]
}
```

## Manual Check

```bash
/usr/bin/python3 ./src/mac_heat_watch.py --print-status
```

To inspect the outgoing Discord payload without sending it:

```bash
/usr/bin/python3 ./src/mac_heat_watch.py --dry-run --print-status
```

No payload is produced when the current temperature is normal and no previous notification is pending recovery.

## 手動確認

```bash
/usr/bin/python3 ./src/mac_heat_watch.py --print-status
```

Discord へ送らず、送信予定の payload だけ確認する場合:

```bash
/usr/bin/python3 ./src/mac_heat_watch.py --dry-run --print-status
```

現在の温度が normal で、直前に通知済みの温度帯がなければ payload は出ません。

## Install as a LaunchAgent

```bash
./scripts/install_launch_agent.sh
```

Uninstall:

```bash
./scripts/uninstall_launch_agent.sh
```

## LaunchAgent としてインストール

```bash
./scripts/install_launch_agent.sh
```

アンインストール:

```bash
./scripts/uninstall_launch_agent.sh
```

## Logs and State

- Logs: `~/Library/Logs/mac-heat-watch/`
- State: `~/Library/Application Support/mac-heat-watch/state.json`
- Optional config: `~/Library/Application Support/mac-heat-watch/config.json`

You can put `discord_warning_webhook_url` directly in `config.json`, but Keychain is recommended for secrets.

## ログと状態

- ログ: `~/Library/Logs/mac-heat-watch/`
- 状態: `~/Library/Application Support/mac-heat-watch/state.json`
- 任意設定: `~/Library/Application Support/mac-heat-watch/config.json`

`discord_warning_webhook_url` を `config.json` に直接置くこともできますが、秘密情報なので Keychain 推奨です。

## License

MIT
