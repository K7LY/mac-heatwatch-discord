#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


APP_NAME = "mac-heat-watch"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / APP_NAME
STATE_PATH = APP_SUPPORT / "state.json"
CONFIG_PATH = APP_SUPPORT / "config.json"
LOCK_PATH = APP_SUPPORT / "watch.lock"
LOG_PATH = Path.home() / "Library" / "Logs" / APP_NAME / "watch.log"


@dataclass(frozen=True)
class Band:
    name: str
    label_ja: str
    min_c: float
    max_c: float | None


DEFAULT_BANDS = [
    Band("normal", "通常", 0.0, 60.0),
    Band("watch", "注意", 60.0, 70.0),
    Band("elevated", "警戒", 70.0, 80.0),
    Band("hot", "高温", 80.0, 90.0),
    Band("danger", "危険", 90.0, None),
]


@dataclass(frozen=True)
class Reading:
    source: str
    machine: str
    timestamp: str
    cpu_temp: float | None
    gpu_temp: float | None

    @property
    def heat_level(self) -> float:
        values = [value for value in (self.cpu_temp, self.gpu_temp) if value is not None]
        if not values:
            raise ValueError("CPU/GPU temperature values are unavailable")
        return max(values)


@dataclass(frozen=True)
class CheckResult:
    exit_code: int
    band: str | None


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line)


def run_command(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def find_executable(name: str, extra_paths: list[str] | None = None) -> str:
    path = shutil.which(name)
    if path:
        return path

    for directory in extra_paths or []:
        candidate = Path(directory) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    return name


def read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"invalid config; using defaults: {exc}")
        return {}


def config_bool(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def config_int(config: dict[str, Any], key: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def load_bands(config: dict[str, Any]) -> list[Band]:
    raw_bands = config.get("temperature_bands", config.get("bands"))
    if not isinstance(raw_bands, list):
        return DEFAULT_BANDS

    bands: list[Band] = []
    try:
        for item in raw_bands:
            if not isinstance(item, dict):
                raise ValueError("band entries must be objects")
            name = str(item["name"])
            label_ja = str(item.get("label_ja", name))
            min_c = float(item.get("min_c", item.get("min")))
            raw_max = item.get("max_c", item.get("max"))
            max_c = None if raw_max is None else float(raw_max)
            if max_c is not None and max_c <= min_c:
                raise ValueError(f"band {name} has max_c <= min_c")
            bands.append(Band(name, label_ja, min_c, max_c))
    except (KeyError, TypeError, ValueError) as exc:
        log(f"invalid temperature_bands config; using defaults: {exc}")
        return DEFAULT_BANDS

    bands.sort(key=lambda band: band.min_c)
    if not bands or bands[0].name != "normal" or bands[-1].max_c is not None:
        log("invalid temperature_bands config; using defaults: first band must be normal and last max_c must be null")
        return DEFAULT_BANDS
    return bands


def band_rank(bands: list[Band]) -> dict[str, int]:
    return {band.name: index for index, band in enumerate(bands)}


def band_label(bands: list[Band], name: str) -> str:
    for band in bands:
        if band.name == name:
            return band.label_ja
    return name


def highest_band_name(bands: list[Band]) -> str:
    return bands[-1].name


def band_by_name(bands: list[Band], name: str) -> Band | None:
    for band in bands:
        if band.name == name:
            return band
    return None


def first_non_normal_band_name(bands: list[Band]) -> str | None:
    for band in bands:
        if band.name != "normal":
            return band.name
    return None


def thresholds_summary(bands: list[Band], include_normal: bool = False) -> str:
    parts = []
    for band in bands:
        if band.name == "normal" and not include_normal:
            continue
        if band.max_c is None:
            parts.append(f"{band.name} {band.min_c:g}℃以上")
        elif band.name == "normal":
            parts.append(f"{band.name} {band.max_c:g}℃未満")
        else:
            parts.append(f"{band.name} {band.min_c:g}℃以上")
    return " / ".join(parts)


def band_threshold_text(bands: list[Band], name: str) -> str:
    band = band_by_name(bands, name)
    if band is None:
        return "設定温度"
    return f"{band.min_c:g}℃"


def normal_threshold_text(bands: list[Band]) -> str:
    normal = band_by_name(bands, "normal")
    if normal is None or normal.max_c is None:
        return "normal"
    return f"{normal.max_c:g}℃未満"


def should_use_active_interval(band: str, bands: list[Band], active_from_band: str | None) -> bool:
    if band == "normal" or active_from_band is None:
        return False
    ranks = band_rank(bands)
    return ranks.get(band, -1) >= ranks.get(active_from_band, len(bands))


def seconds_since(timestamp: Any, now: datetime) -> float | None:
    if not isinstance(timestamp, str):
        return None
    try:
        previous = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    return (now - previous).total_seconds()


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(state: dict[str, Any]) -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp_path.replace(STATE_PATH)


def acquire_lock() -> Any | None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def load_webhook_url(config: dict[str, Any]) -> str | None:
    env_value = os.environ.get("DISCORD_WARNING_WEBHOOK_URL")
    if env_value:
        return env_value

    keychain_service = config.get("keychain_service", "DISCORD_WARNING_WEBHOOK_URL")
    result = run_command(["security", "find-generic-password", "-s", keychain_service, "-w"], timeout=10)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    configured = config.get("discord_warning_webhook_url")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()

    return None


def read_macmon(samples: int, interval_ms: int) -> Reading:
    macmon_path = find_executable("macmon", ["/opt/homebrew/bin", "/usr/local/bin"])
    result = run_command(
        [macmon_path, "pipe", "--samples", str(samples), "--interval", str(interval_ms), "--soc-info"],
        timeout=max(20, samples * interval_ms // 1000 + 10),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "macmon failed")

    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if not records:
        raise RuntimeError("macmon returned no samples")

    latest = records[-1]
    temp = latest.get("temp", {})
    soc = latest.get("soc", {})
    cpu_temp = temp.get("cpu_temp_avg")
    gpu_temp = temp.get("gpu_temp_avg")
    return Reading(
        source="macmon",
        machine=f"{soc.get('chip_name', 'unknown chip')} ({soc.get('mac_model', 'unknown model')})",
        timestamp=latest.get("timestamp", datetime.now().isoformat()),
        cpu_temp=float(cpu_temp) if cpu_temp is not None else None,
        gpu_temp=float(gpu_temp) if gpu_temp is not None else None,
    )


def band_for(temp_c: float, bands: list[Band]) -> str:
    for band in bands:
        if temp_c >= band.min_c and (band.max_c is None or temp_c < band.max_c):
            return band.name
    return "normal"


def top_processes(sort_flag: str) -> list[dict[str, str]]:
    try:
        result = run_command(["ps", sort_flag, "-xo", "pid,pcpu,pmem,comm"], timeout=10)
    except Exception as exc:
        log(f"process summary skipped: ps failed: {exc}")
        return []
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines()[1:8]:
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid, cpu, mem, command = parts
        rows.append({"pid": pid, "cpu": cpu, "mem": mem, "command": command})
    return rows


def logical_cpu_count() -> int:
    try:
        result = run_command(["sysctl", "-n", "hw.ncpu"], timeout=5)
    except Exception as exc:
        log(f"process summary using fallback CPU count: {exc}")
        return 1
    if result.returncode != 0:
        return 1
    try:
        return max(1, int(result.stdout.strip()))
    except ValueError:
        return 1


def summarize_processes() -> str:
    cpu_rows = top_processes("-arc")
    mem_rows = top_processes("-arm")
    cpu_count = logical_cpu_count()

    def compact(rows: list[dict[str, str]], metric: str, limit: int = 5) -> str:
        items = []
        for row in rows[:limit]:
            command = Path(row["command"]).name or row["command"]
            if metric == "cpu":
                try:
                    value = float(row["cpu"]) / cpu_count
                except ValueError:
                    value = 0.0
                items.append(f"{command} {value:.1f}%")
            else:
                items.append(f"{command} {row['mem']}%")
        return "、".join(items) if items else "取得できませんでした"

    return (
        f"CPU上位（全体比）: {compact(cpu_rows, 'cpu')}\n"
        f"メモリ上位: {compact(mem_rows, 'mem', limit=3)}"
    )


def format_temp(value: float | None) -> str:
    return "不明" if value is None else f"{value:.1f}℃"


def build_discord_message(reading: Reading, band: str, bands: list[Band], process_summary: str) -> str:
    return "\n".join(
        [
            f"Mac mini の温度が {band_threshold_text(bands, band)}を超えました",
            f"機種: {reading.machine}",
            f"時刻: {reading.timestamp}",
            f"CPU温度: {format_temp(reading.cpu_temp)} / GPU温度: {format_temp(reading.gpu_temp)}",
            "原因候補:",
            process_summary,
            "************",
        ]
    )[:1900]


def build_recovery_message(reading: Reading, previous_band: str, bands: list[Band]) -> str:
    return "\n".join(
        [
            f"Mac mini の温度が {normal_threshold_text(bands)}に戻りました",
            f"機種: {reading.machine}",
            f"時刻: {reading.timestamp}",
            f"CPU温度: {format_temp(reading.cpu_temp)} / GPU温度: {format_temp(reading.gpu_temp)}",
            "************",
        ]
    )[:1900]


def post_discord(webhook_url: str, content: str, dry_run: bool) -> None:
    payload = {
        "content": content,
        "allowed_mentions": {"parse": []},
    }
    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": f"{APP_NAME}/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status not in (200, 204):
                raise RuntimeError(f"Discord returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Discord returned HTTP {exc.code}: {body}") from exc


def should_notify(
    state: dict[str, Any],
    band: str,
    bands: list[Band],
    repeat_highest_band: bool,
    highest_band_repeat_interval_seconds: int,
    now: datetime,
) -> bool:
    if band == "normal":
        return False
    last_notified = state.get("last_notified_band")

    if band == highest_band_name(bands):
        if state.get("last_notified_band") != band:
            return True
        if not repeat_highest_band:
            return False
        if highest_band_repeat_interval_seconds <= 0:
            return True
        elapsed = seconds_since(state.get("last_notified_at"), now)
        if elapsed is None:
            return True
        return elapsed >= highest_band_repeat_interval_seconds

    if not isinstance(last_notified, str):
        return True
    ranks = band_rank(bands)
    return ranks[band] > ranks.get(last_notified, -1)


def check_once(
    args: argparse.Namespace,
    config: dict[str, Any],
    bands: list[Band],
    notify_on_recovery: bool,
    repeat_highest_band: bool,
    highest_band_repeat_interval_seconds: int,
) -> CheckResult:
    state = read_state()

    try:
        reading = read_macmon(args.samples, args.interval_ms)
    except Exception as exc:
        log(f"temperature read failed: {exc}")
        print(f"温度取得に失敗しました: {exc}", file=sys.stderr)
        return CheckResult(1, None)

    band = band_for(reading.heat_level, bands)
    now = datetime.now()
    now_iso = now.isoformat(timespec="seconds")

    if band == "normal":
        last_notified = state.get("last_notified_band")
        if notify_on_recovery and isinstance(last_notified, str):
            webhook_url = load_webhook_url(config)
            if not webhook_url:
                log("recovery notification skipped: DISCORD_WARNING_WEBHOOK_URL is missing")
                print("DISCORD_WARNING_WEBHOOK_URL が見つからないため復旧通知できません。", file=sys.stderr)
                return CheckResult(2, band)
            post_discord(webhook_url, build_recovery_message(reading, last_notified, bands), args.dry_run)
            log(f"recovered: from {last_notified} CPU {format_temp(reading.cpu_temp)} / GPU {format_temp(reading.gpu_temp)}")

        write_state({
            "last_seen_at": now_iso,
            "last_band": band,
            "last_cpu_temp": reading.cpu_temp,
            "last_gpu_temp": reading.gpu_temp,
            "last_notified_band": None,
        })
        message = f"normal: CPU {format_temp(reading.cpu_temp)} / GPU {format_temp(reading.gpu_temp)}"
        log(message)
        if args.print_status:
            print(message)
        return CheckResult(0, band)

    if not should_notify(state, band, bands, repeat_highest_band, highest_band_repeat_interval_seconds, now):
        write_state({
            **state,
            "last_seen_at": now_iso,
            "last_band": band,
            "last_cpu_temp": reading.cpu_temp,
            "last_gpu_temp": reading.gpu_temp,
        })
        message = f"suppressed: {band} CPU {format_temp(reading.cpu_temp)} / GPU {format_temp(reading.gpu_temp)}"
        log(message)
        if args.print_status:
            print(message)
        return CheckResult(0, band)

    webhook_url = load_webhook_url(config)
    if not webhook_url:
        log("notification skipped: DISCORD_WARNING_WEBHOOK_URL is missing")
        print("DISCORD_WARNING_WEBHOOK_URL が見つからないため通知できません。", file=sys.stderr)
        return CheckResult(2, band)

    try:
        process_summary = summarize_processes()
    except Exception as exc:
        log(f"process summary failed; sending temperature alert anyway: {exc}")
        process_summary = "取得失敗（温度警報を優先して送信）"
    content = build_discord_message(reading, band, bands, process_summary)
    post_discord(webhook_url, content, args.dry_run)

    write_state({
        "last_seen_at": now_iso,
        "last_band": band,
        "last_cpu_temp": reading.cpu_temp,
        "last_gpu_temp": reading.gpu_temp,
        "last_notified_band": band,
        "last_notified_at": now_iso,
    })
    log(f"notified: {band} CPU {format_temp(reading.cpu_temp)} / GPU {format_temp(reading.gpu_temp)}")
    if args.print_status:
        print(f"notified: {band}")
    return CheckResult(0, band)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor Mac chip temperature and notify Discord.")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-status", action="store_true")
    parser.add_argument("--once", action="store_true", help="Run one check and exit, ignoring active monitoring.")
    args = parser.parse_args()

    lock_handle = acquire_lock()
    if lock_handle is None:
        log("another watcher instance is already running; exiting")
        return 0

    config = read_config()
    bands = load_bands(config)
    notify_on_recovery = config_bool(config, "notify_on_recovery", True)
    repeat_highest_band = config_bool(config, "repeat_highest_band", True)
    active_interval_seconds = config_int(config, "active_interval_seconds", 0, minimum=0)
    highest_repeat_interval_seconds = config_int(config, "highest_band_repeat_interval_seconds", 0, minimum=0)
    active_from_band = str(config.get("active_from_band") or first_non_normal_band_name(bands) or "")

    while True:
        result = check_once(
            args,
            config,
            bands,
            notify_on_recovery,
            repeat_highest_band,
            highest_repeat_interval_seconds,
        )
        if result.exit_code != 0:
            return result.exit_code
        if args.once or active_interval_seconds <= 0:
            return 0
        if result.band is None or not should_use_active_interval(result.band, bands, active_from_band):
            return 0
        log(f"active monitoring: band={result.band} sleeping {active_interval_seconds}s")
        time.sleep(active_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
