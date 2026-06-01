#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
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
LOG_PATH = Path.home() / "Library" / "Logs" / APP_NAME / "watch.log"

BANDS = [
    ("normal", 0.0, 60.0),
    ("watch", 60.0, 70.0),
    ("elevated", 70.0, 80.0),
    ("hot", 80.0, 90.0),
    ("danger", 90.0, None),
]
BAND_RANK = {name: index for index, (name, _, _) in enumerate(BANDS)}
BAND_LABEL_JA = {
    "normal": "通常",
    "watch": "注意",
    "elevated": "警戒",
    "hot": "高温",
    "danger": "危険",
}


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
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def band_for(temp_c: float) -> str:
    for name, low, high in BANDS:
        if temp_c >= low and (high is None or temp_c < high):
            return name
    return "normal"


def top_processes(sort_flag: str) -> list[dict[str, str]]:
    result = run_command(["ps", sort_flag, "-xo", "pid,pcpu,pmem,comm"], timeout=10)
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


def summarize_processes() -> str:
    cpu_rows = top_processes("-arc")
    mem_rows = top_processes("-arm")

    def compact(rows: list[dict[str, str]], metric: str, limit: int = 5) -> str:
        items = []
        for row in rows[:limit]:
            command = Path(row["command"]).name or row["command"]
            value = row["cpu"] if metric == "cpu" else row["mem"]
            items.append(f"{command} {value}%")
        return "、".join(items) if items else "取得できませんでした"

    return (
        f"CPU上位: {compact(cpu_rows, 'cpu')}\n"
        f"メモリ上位: {compact(mem_rows, 'mem', limit=3)}"
    )


def format_temp(value: float | None) -> str:
    return "不明" if value is None else f"{value:.1f}℃"


def build_discord_message(reading: Reading, band: str, process_summary: str) -> str:
    severity = "危険" if band == "danger" else "注意"
    return "\n".join(
        [
            f"{severity}: Mac mini の温度が {band} 帯（{BAND_LABEL_JA[band]}）に入りました",
            f"機種: {reading.machine}",
            f"時刻: {reading.timestamp}",
            f"CPU温度: {format_temp(reading.cpu_temp)} / GPU温度: {format_temp(reading.gpu_temp)}",
            f"現在の温度帯: {band}",
            "基準: watch 60℃以上 / elevated 70℃以上 / hot 80℃以上 / danger 90℃以上",
            "原因候補:",
            process_summary,
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


def should_notify(state: dict[str, Any], band: str) -> bool:
    if band == "normal":
        return False
    last_notified = state.get("last_notified_band")
    if not isinstance(last_notified, str):
        return True
    return BAND_RANK[band] > BAND_RANK.get(last_notified, -1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor Mac chip temperature and notify Discord.")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-status", action="store_true")
    args = parser.parse_args()

    config = read_config()
    state = read_state()

    try:
        reading = read_macmon(args.samples, args.interval_ms)
    except Exception as exc:
        log(f"temperature read failed: {exc}")
        print(f"温度取得に失敗しました: {exc}", file=sys.stderr)
        return 1

    band = band_for(reading.heat_level)
    now = datetime.now().isoformat(timespec="seconds")

    if band == "normal":
        write_state({
            "last_seen_at": now,
            "last_band": band,
            "last_cpu_temp": reading.cpu_temp,
            "last_gpu_temp": reading.gpu_temp,
            "last_notified_band": None,
        })
        message = f"normal: CPU {format_temp(reading.cpu_temp)} / GPU {format_temp(reading.gpu_temp)}"
        log(message)
        if args.print_status:
            print(message)
        return 0

    if not should_notify(state, band):
        write_state({
            **state,
            "last_seen_at": now,
            "last_band": band,
            "last_cpu_temp": reading.cpu_temp,
            "last_gpu_temp": reading.gpu_temp,
        })
        message = f"suppressed: {band} CPU {format_temp(reading.cpu_temp)} / GPU {format_temp(reading.gpu_temp)}"
        log(message)
        if args.print_status:
            print(message)
        return 0

    webhook_url = load_webhook_url(config)
    if not webhook_url:
        log("notification skipped: DISCORD_WARNING_WEBHOOK_URL is missing")
        print("DISCORD_WARNING_WEBHOOK_URL が見つからないため通知できません。", file=sys.stderr)
        return 2

    process_summary = summarize_processes()
    content = build_discord_message(reading, band, process_summary)
    post_discord(webhook_url, content, args.dry_run)

    write_state({
        "last_seen_at": now,
        "last_band": band,
        "last_cpu_temp": reading.cpu_temp,
        "last_gpu_temp": reading.gpu_temp,
        "last_notified_band": band,
        "last_notified_at": now,
    })
    log(f"notified: {band} CPU {format_temp(reading.cpu_temp)} / GPU {format_temp(reading.gpu_temp)}")
    if args.print_status:
        print(f"notified: {band}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
