#!/usr/bin/env python3
"""Fetch Codex usage limits and reset credits."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_AUTH_PATH = Path("~/.codex/auth.json").expanduser()
DEFAULT_RESETS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
DEFAULT_USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"
DEFAULT_RESET_FULL_DAYS = 30.0
DEFAULT_TIMEOUT_SECONDS = 20.0
WEEKLY_WINDOW_SECONDS = 604800
ACTIVE_TIMEZONE = ZoneInfo("Asia/Tokyo")
ACTIVE_START = time(9)
ACTIVE_END = time(20)
ACTIVE_WEEKLY_LABEL = "利用時間残り (09-20)"
BAR_WIDTH = 20
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_RESET = "\033[0m"
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def load_json(path: Path) -> Any:
  with path.open("r", encoding="utf-8") as handle:
    return json.load(handle)


def access_token_from_auth(auth: dict[str, Any]) -> str:
  tokens = auth.get("tokens")
  if isinstance(tokens, dict) and isinstance(tokens.get("access_token"), str) and tokens["access_token"]:
    return tokens["access_token"]

  raise SystemExit("No usable tokens.access_token found in auth.json")


def account_id_from_auth(auth: dict[str, Any]) -> str | None:
  tokens = auth.get("tokens")
  if not isinstance(tokens, dict):
    return None
  account_id = tokens.get("account_id")
  return account_id if isinstance(account_id, str) and account_id else None


def fetch_response(auth_path: Path, url: str, timeout: float, label: str) -> Any:
  auth = load_json(auth_path)
  token = access_token_from_auth(auth)

  headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/json",
    "User-Agent": "codex-limits/1.0",
  }
  account_id = account_id_from_auth(auth)
  if account_id:
    headers["ChatGPT-Account-ID"] = account_id

  request = urllib.request.Request(url, headers=headers, method="GET")
  try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
      body = response.read().decode("utf-8")
  except urllib.error.HTTPError as error:
    detail = error.read().decode("utf-8", errors="replace")
    raise SystemExit(f"HTTP {error.code} from {label} endpoint: {safe_error(detail)}") from error
  except urllib.error.URLError as error:
    raise SystemExit(f"Failed to reach {label} endpoint: {error.reason}") from error

  return json.loads(body)


def safe_error(value: str) -> str:
  compact = " ".join(value.split())
  if len(compact) > 240:
    return compact[:237] + "..."
  return compact


def find_reset_credit_records(payload: Any) -> list[dict[str, Any]]:
  return find_records(
    payload,
    candidate_keys=(
      "credits",
      "reset_credits",
      "rate_limit_reset_credits",
      "rateLimitResetCredits",
      "items",
      "data",
      "results",
    ),
    looks_like=looks_like_credit,
  )


def find_usage_records(payload: Any) -> list[dict[str, Any]]:
  codex_usage_records = find_codex_usage_records(payload)
  if codex_usage_records:
    return codex_usage_records

  return find_records(
    payload,
    candidate_keys=(
      "usage",
      "usages",
      "limits",
      "rate_limits",
      "rateLimits",
      "codex_usage",
      "codexUsage",
      "additional_rate_limits",
      "additionalRateLimits",
      "items",
      "data",
      "results",
    ),
    looks_like=looks_like_usage,
  )


def find_codex_usage_records(payload: Any) -> list[dict[str, Any]]:
  if not isinstance(payload, dict):
    return []

  records: list[dict[str, Any]] = []
  append_rate_limit_records(records, payload.get("rate_limit"))

  additional_limits = payload.get("additional_rate_limits")
  if isinstance(additional_limits, list):
    for additional_limit in additional_limits:
      if not isinstance(additional_limit, dict):
        continue
      limit_name = first_present(
        additional_limit,
        ("limit_name", "limitName", "name", "title", "label", "metered_feature", "meteredFeature"),
      )
      append_rate_limit_records(records, additional_limit.get("rate_limit"), display_value(limit_name))

  return records


def append_rate_limit_records(records: list[dict[str, Any]], rate_limit: Any, limit_name: str | None = None) -> None:
  if not isinstance(rate_limit, dict):
    return

  for window_key in ("primary_window", "secondary_window"):
    window = rate_limit.get(window_key)
    if not isinstance(window, dict):
      continue

    used_percent = first_present(window, ("used_percent", "usedPercent"))
    record = {
      "title": usage_limit_title(limit_name, window_key, window),
      "remaining_percent": remaining_from_used_percent(used_percent),
      "used_display": used_percent_display(used_percent),
      "reset_at": first_present(window, ("reset_at", "resetAt")),
      "window_seconds": first_present(window, ("limit_window_seconds", "limitWindowSeconds")),
      "status": rate_limit_status(rate_limit),
    }
    records.append(record)


def usage_limit_title(limit_name: str | None, window_key: str, window: dict[str, Any]) -> str:
  window_name = usage_window_name(window_key, window)
  if limit_name and limit_name != "unknown":
    return f"{limit_name} {window_name}"
  return window_name


def usage_window_name(window_key: str, window: dict[str, Any]) -> str:
  seconds = first_number(window, ("limit_window_seconds", "limitWindowSeconds"))
  if seconds == 18000:
    return "5時間の使用制限"
  if seconds == 604800:
    return "週間利用上限"
  if window_key == "primary_window":
    return "Primary window"
  if window_key == "secondary_window":
    return "Secondary window"
  return window_key


def remaining_from_used_percent(used_percent: Any) -> Any:
  number = number_from_value(used_percent)
  if number is None:
    return None
  return max(0.0, 100.0 - number)


def used_percent_display(used_percent: Any) -> str:
  number = number_from_value(used_percent)
  if number is None:
    return "unknown"
  return f"{display_percent(number)} used"


def rate_limit_status(rate_limit: dict[str, Any]) -> str:
  limit_reached = rate_limit.get("limit_reached")
  allowed = rate_limit.get("allowed")
  if limit_reached is True:
    return "reached"
  if allowed is False:
    return "blocked"
  if allowed is True:
    return "ok"
  return "unknown"


def find_records(
  payload: Any,
  *,
  candidate_keys: tuple[str, ...],
  looks_like: Any,
) -> list[dict[str, Any]]:
  if isinstance(payload, list):
    records = []
    for item in payload:
      if isinstance(item, dict):
        if looks_like(item):
          records.append(item)
        else:
          records.extend(find_records(item, candidate_keys=candidate_keys, looks_like=looks_like))
    return records

  if not isinstance(payload, dict):
    return []

  for key, value in payload.items():
    if key not in candidate_keys:
      continue
    if isinstance(value, list):
      records = []
      for item in value:
        if isinstance(item, dict):
          record = dict(item)
          if not record.get("name") and isinstance(key, str):
            record.setdefault("source", key)
          records.append(record)
      if records:
        return records
    if isinstance(value, dict):
      nested = find_records(value, candidate_keys=candidate_keys, looks_like=looks_like)
      if nested:
        return nested

  if looks_like(payload):
    return [payload]

  mapped_records = mapped_dict_records(payload, looks_like)
  if mapped_records:
    return mapped_records

  for value in payload.values():
    nested = find_records(value, candidate_keys=candidate_keys, looks_like=looks_like)
    if nested:
      return nested

  return []


def mapped_dict_records(payload: dict[str, Any], looks_like: Any) -> list[dict[str, Any]]:
  records = []
  for key, value in payload.items():
    if not isinstance(value, dict) or not looks_like(value):
      continue
    record = dict(value)
    record.setdefault("name", key)
    records.append(record)
  return records


def looks_like_credit(record: dict[str, Any]) -> bool:
  keys = {key.lower() for key in record}
  return bool(
    keys
    & {
      "status",
      "state",
      "issued_at",
      "issuedat",
      "expires_at",
      "expiresat",
      "expiration",
      "redeemed",
      "is_redeemed",
      "isredeemed",
    }
  )


def looks_like_usage(record: dict[str, Any]) -> bool:
  keys = {key.lower() for key in record}
  return bool(
    keys
    & {
      "remaining_percent",
      "remainingpercent",
      "percent_remaining",
      "percentremaining",
      "remaining_percentage",
      "remainingpercentage",
      "remaining_ratio",
      "remainingratio",
      "reset_at",
      "resetat",
      "resets_at",
      "resetsat",
      "used",
      "limit",
      "usage",
      "cap",
      "window",
      "period",
    }
  )


def first_present(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
  for key in keys:
    if key in record:
      return record[key]
  return None


def normalize_usage_record(record: dict[str, Any]) -> dict[str, str]:
  remaining = usage_remaining(record)
  reset_value = first_present(
    record,
    (
      "reset_at",
      "resetAt",
      "resets_at",
      "resetsAt",
      "next_reset_at",
      "nextResetAt",
      "refresh_at",
      "refreshAt",
    ),
  )
  window_seconds = first_number(record, ("window_seconds", "windowSeconds", "limit_window_seconds", "limitWindowSeconds"))
  window_remaining = window_remaining_percent(reset_value, window_seconds)
  return {
    "limit": usage_name(record),
    "remaining": remaining,
    "remaining_bar": percent_bar(percent_number_from_display(remaining)),
    "used": usage_used(record),
    "reset": display_datetime_value(reset_value),
    "status": display_value(first_present(record, ("status", "state"))),
    "window_remaining": display_percent(window_remaining) if window_remaining is not None else "unknown",
    "window_remaining_bar": percent_bar(window_remaining),
  }


def usage_name(record: dict[str, Any]) -> str:
  explicit = first_present(
    record,
    (
      "name",
      "title",
      "label",
      "display_name",
      "displayName",
      "limit_name",
      "limitName",
      "metric",
      "source",
    ),
  )
  if explicit is not None:
    return display_value(explicit)

  model = first_present(record, ("model", "model_slug", "modelSlug"))
  window = first_present(record, ("window", "period", "bucket", "interval"))
  parts = [display_value(part) for part in (model, window) if part is not None]
  return " ".join(parts) if parts else "unknown"


def usage_remaining(record: dict[str, Any]) -> str:
  explicit_percent = first_present(
    record,
    (
      "remaining_percent",
      "remainingPercent",
      "percent_remaining",
      "percentRemaining",
      "remaining_percentage",
      "remainingPercentage",
      "remaining_pct",
      "remainingPct",
    ),
  )
  if explicit_percent is not None:
    return display_percent(explicit_percent)

  ratio = first_present(record, ("remaining_ratio", "remainingRatio"))
  if ratio is not None:
    return display_percent(ratio, ratio=True)

  remaining = first_number(record, ("remaining", "remaining_units", "remainingUnits"))
  limit = first_number(record, ("limit", "cap", "quota", "total"))
  if remaining is not None and limit and limit > 0:
    return display_percent((remaining / limit) * 100)

  used = first_number(record, ("used", "usage", "consumed", "used_units", "usedUnits"))
  if used is not None and limit and limit > 0:
    return display_percent(((limit - used) / limit) * 100)

  return "unknown"


def usage_used(record: dict[str, Any]) -> str:
  explicit_display = first_present(record, ("used_display", "usedDisplay", "usage_display", "usageDisplay"))
  if explicit_display is not None:
    return display_value(explicit_display)

  used = first_number(record, ("used", "usage", "consumed", "used_units", "usedUnits"))
  limit = first_number(record, ("limit", "cap", "quota", "total"))
  remaining = first_number(record, ("remaining", "remaining_units", "remainingUnits"))

  if used is not None and limit is not None:
    return f"{display_number(used)} / {display_number(limit)}"
  if remaining is not None and limit is not None:
    return f"{display_number(limit - remaining)} / {display_number(limit)}"
  if used is not None:
    return display_number(used)
  if remaining is not None:
    return f"remaining {display_number(remaining)}"
  return "unknown"


def first_number(record: dict[str, Any], keys: tuple[str, ...]) -> float | None:
  value = first_present(record, keys)
  return number_from_value(value)


def number_from_value(value: Any) -> float | None:
  if isinstance(value, bool) or value is None:
    return None
  if isinstance(value, (int, float)):
    return float(value)
  if isinstance(value, str):
    try:
      return float(value)
    except ValueError:
      return None
  return None


def display_percent(value: Any, *, ratio: bool = False) -> str:
  if isinstance(value, bool):
    return display_value(value)
  try:
    number = float(value)
  except (TypeError, ValueError):
    return display_value(value)

  if ratio or 0 <= number <= 1:
    number *= 100
  rounded = round(number, 1)
  if rounded.is_integer():
    return f"{int(rounded)}%"
  return f"{rounded}%"


def display_number(value: float) -> str:
  if value.is_integer():
    return str(int(value))
  return str(round(value, 2))


def normalize_reset_record(record: dict[str, Any], reset_full_days: float) -> dict[str, str]:
  expires_value = first_present(record, ("expires_at", "expiresAt", "expiration", "expiration_at", "expirationAt"))
  days_left = expiry_days_left(expires_value)
  days_percent = None if days_left is None else min(100.0, max(0.0, (days_left / reset_full_days) * 100))
  return {
    "status": display_value(first_present(record, ("status", "state"))),
    "issued": display_datetime_value(
      first_present(record, ("issued_at", "issuedAt", "granted_at", "grantedAt", "created_at", "createdAt"))
    ),
    "expires": display_datetime_value(expires_value),
    "time_left": display_days_left(days_left),
    "time_left_bar": percent_bar(days_percent),
    "urgency": reset_urgency(days_left),
    "redeemed": display_reset_redeemed(record),
  }


def display_reset_redeemed(record: dict[str, Any]) -> str:
  for key in ("redeemed", "is_redeemed", "isRedeemed"):
    if key in record:
      return display_redeemed_value(record[key])

  for key in ("redeemed_at", "redeemedAt"):
    if key in record:
      return "yes" if record[key] else "no"

  status = display_value(first_present(record, ("status", "state"))).lower()
  if status in {"available", "active", "unused"}:
    return "no"
  if status in {"redeemed", "used"}:
    return "yes"
  return "unknown"


def display_redeemed_value(value: Any) -> str:
  if isinstance(value, bool):
    return "yes" if value else "no"
  return display_value(value)


def display_datetime_value(value: Any) -> str:
  parsed = parse_datetime_value(value)
  if parsed is not None:
    return parsed.astimezone().strftime("%m-%d %H:%M %Z")
  return display_value(value)


def parse_datetime_value(value: Any) -> datetime | None:
  if isinstance(value, bool) or value is None:
    return None
  if isinstance(value, (int, float)) and value > 1_000_000_000:
    return datetime.fromtimestamp(value).astimezone()
  if isinstance(value, str):
    stripped = value.strip()
    if stripped.isdigit() and int(stripped) > 1_000_000_000:
      return datetime.fromtimestamp(int(stripped)).astimezone()
    if "-" in stripped:
      try:
        return datetime.fromisoformat(stripped.replace("Z", "+00:00")).astimezone()
      except ValueError:
        return None
  return None


def expiry_days_left(value: Any) -> float | None:
  parsed = parse_datetime_value(value)
  if parsed is None:
    return None
  seconds_left = (parsed - datetime.now().astimezone()).total_seconds()
  return max(0.0, seconds_left / 86400)


def window_remaining_percent(
  reset_value: Any,
  window_seconds: float | None,
  now: datetime | None = None,
) -> float | None:
  reset_at = parse_datetime_value(reset_value)
  if reset_at is None or window_seconds is None or window_seconds <= 0:
    return None
  current_time = now.astimezone() if now is not None else datetime.now().astimezone()
  if window_seconds == WEEKLY_WINDOW_SECONDS:
    window_start = reset_at - timedelta(seconds=window_seconds)
    active_window_seconds = active_seconds_between(window_start, reset_at)
    if active_window_seconds <= 0:
      return None
    active_seconds_left = active_seconds_between(max(current_time, window_start), reset_at)
    return min(100.0, max(0.0, (active_seconds_left / active_window_seconds) * 100))
  reset_after_seconds = (reset_at - current_time).total_seconds()
  return min(100.0, max(0.0, (reset_after_seconds / window_seconds) * 100))


def active_seconds_between(start: datetime, end: datetime) -> float:
  if end <= start:
    return 0.0

  local_start = start.astimezone(ACTIVE_TIMEZONE)
  local_end = end.astimezone(ACTIVE_TIMEZONE)
  total_seconds = 0.0
  current_date = local_start.date()
  while current_date <= local_end.date():
    active_start = datetime.combine(current_date, ACTIVE_START, ACTIVE_TIMEZONE)
    active_end = datetime.combine(current_date, ACTIVE_END, ACTIVE_TIMEZONE)
    overlap_start = max(local_start, active_start)
    overlap_end = min(local_end, active_end)
    if overlap_end > overlap_start:
      total_seconds += (overlap_end - overlap_start).total_seconds()
    current_date += timedelta(days=1)
  return total_seconds


def display_days_left(value: float | None) -> str:
  if value is None:
    return "unknown"
  if value <= 0:
    return "expired"
  if value < 1 / 24:
    minutes = max(1, min(59, round(value * 24 * 60)))
    return f"{minutes}m"
  if value < 1:
    hours = max(1, round(value * 24))
    return f"{hours}h"
  return f"{value:.1f}d"


def reset_urgency(days_left: float | None) -> str:
  if days_left is None or days_left >= 1:
    return ""
  if days_left < 2 / 24:
    return "error"
  return "warning"


def percent_number_from_display(value: str) -> float | None:
  stripped = value.strip()
  if not stripped.endswith("%"):
    return None
  try:
    return float(stripped[:-1])
  except ValueError:
    return None


def percent_bar(value: float | None) -> str:
  if value is None:
    return "[????????????????????] unknown"
  percent = min(100.0, max(0.0, value))
  if percent >= 99.5:
    percent = 100.0
  filled = round((percent / 100) * BAR_WIDTH)
  return f"[{'#' * filled}{'-' * (BAR_WIDTH - filled)}] {display_percent(percent / 100, ratio=True)}"


def display_value(value: Any) -> str:
  if value is None:
    return "unknown"
  if isinstance(value, bool):
    return "yes" if value else "no"
  if isinstance(value, (int, float)):
    return str(value)
  if isinstance(value, str):
    return clean_terminal_cell(value)
  return clean_terminal_cell(json.dumps(value, ensure_ascii=False, sort_keys=True))


def clean_terminal_cell(value: str) -> str:
  return value.replace("\n", " ").replace("\r", " ").strip() or "unknown"


def normalize_usage_records(records: list[dict[str, Any]]) -> list[dict[str, str]]:
  return [normalize_usage_record(record) for record in records]


def normalize_reset_records(records: list[dict[str, Any]], reset_full_days: float) -> list[dict[str, str]]:
  if reset_full_days <= 0:
    raise SystemExit("--reset-full-days must be greater than 0")
  return [normalize_reset_record(record, reset_full_days) for record in records]


def render_terminal(
  usage_records: list[dict[str, str]],
  reset_records: list[dict[str, str]],
) -> str:
  lines = [
    "Codex limits",
    "",
    "Usage availability",
  ]

  if usage_records:
    lines.extend(render_current_usage_tables(usage_records))
  else:
    lines.append("No usage limits found.")

  lines.extend(("", "Reset credits"))
  if reset_records:
    lines.extend(render_table(("#", "Status", "Time left", "Bar", "Issued", "Expires"), reset_rows(reset_records)))
  else:
    lines.append("No reset credits found.")

  return "\n".join(lines).rstrip()


def render_current_usage_tables(records: list[dict[str, str]]) -> list[str]:
  time_left_rows = window_time_left_rows(records)
  label_width = max(
    3,
    *(display_width(record["limit"]) for record in records),
    *(display_width(row[0]) for row in time_left_rows),
  )
  lines = []
  if time_left_rows:
    window_column_width = 3 + 2 + label_width
    lines.extend(render_table(("Window", "Time left", "Reset"), color_weekly_bars(time_left_rows, 1), min_widths={0: window_column_width}))
    lines.extend(("", "Usage limits"))
  lines.extend(render_table(("#", "Limit", "Bar", "Reset"), color_weekly_bars(usage_rows(records), 2), min_widths={1: label_width}))
  return lines


def usage_rows(records: list[dict[str, str]]) -> list[tuple[str, ...]]:
  return [
    (
      str(index),
      record["limit"],
      record["remaining_bar"],
      record["reset"],
    )
    for index, record in enumerate(records, start=1)
  ]


def window_time_left_rows(records: list[dict[str, str]]) -> list[tuple[str, ...]]:
  rows = []
  for window_name in ("5時間の使用制限", "週間利用上限"):
    record = next((candidate for candidate in records if candidate["limit"] == window_name), None)
    if record is None:
      continue
    display_name = ACTIVE_WEEKLY_LABEL if window_name == "週間利用上限" else window_name
    rows.append((display_name, record["window_remaining_bar"], record["reset"]))
  return rows


def color_weekly_bars(rows: list[tuple[str, ...]], bar_index: int) -> list[tuple[str, ...]]:
  colored_rows = []
  for row in rows:
    values = list(row)
    if any("週間" in value or ACTIVE_WEEKLY_LABEL in value for value in values):
      values[bar_index] = yellow(values[bar_index])
    colored_rows.append(tuple(values))
  return colored_rows


def yellow(value: str) -> str:
  return f"{ANSI_YELLOW}{value}{ANSI_RESET}"


def color_row(row: tuple[str, ...], color: str) -> tuple[str, ...]:
  if not color:
    return row
  values = list(row)
  values[0] = f"{color}{values[0]}"
  values[-1] = f"{values[-1]}{ANSI_RESET}"
  return tuple(values)


def reset_rows(records: list[dict[str, str]]) -> list[tuple[str, ...]]:
  rows = []
  for index, record in enumerate(records, start=1):
    row = (
      str(index),
      record["status"],
      record["time_left"],
      record["time_left_bar"],
      record["issued"],
      record["expires"],
    )
    color = ANSI_RED if record.get("urgency") == "error" else ANSI_YELLOW if record.get("urgency") == "warning" else ""
    rows.append(color_row(row, color))
  return rows


def render_table(
  headers: tuple[str, ...],
  rows: list[tuple[str, ...]],
  *,
  min_widths: dict[int, int] | None = None,
) -> list[str]:
  min_widths = min_widths or {}
  widths = [
    max(min_widths.get(column, 3), display_width(headers[column]), *(display_width(row[column]) for row in rows))
    for column in range(len(headers))
  ]
  return [
    format_row(headers, widths, right_align_first=headers[0] == "#"),
    format_separator(widths),
    *(format_row(row, widths, right_align_first=headers[0] == "#") for row in rows),
  ]


def format_row(row: tuple[str, ...], widths: list[int], *, right_align_first: bool) -> str:
  return "  ".join(
    pad_cell(value, width, right_align=right_align_first and index == 0)
    for index, (value, width) in enumerate(zip(row, widths))
  )


def format_separator(widths: list[int]) -> str:
  return "  ".join("-" * width for width in widths)


def pad_cell(value: str, width: int, *, right_align: bool) -> str:
  padding = max(0, width - display_width(value))
  if right_align:
    return (" " * padding) + value
  return value + (" " * padding)


def display_width(value: str) -> int:
  value = ANSI_PATTERN.sub("", value)
  width = 0
  for character in value:
    if unicodedata.combining(character):
      continue
    width += 2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
  return width


def main() -> int:
  if len(sys.argv) > 1:
    raise SystemExit("coli does not take options")

  usage_payload = fetch_response(DEFAULT_AUTH_PATH, DEFAULT_USAGE_URL, DEFAULT_TIMEOUT_SECONDS, "usage limits")
  resets_payload = fetch_response(DEFAULT_AUTH_PATH, DEFAULT_RESETS_URL, DEFAULT_TIMEOUT_SECONDS, "reset credits")
  usage_records = normalize_usage_records(find_usage_records(usage_payload))
  reset_records = normalize_reset_records(find_reset_credit_records(resets_payload), DEFAULT_RESET_FULL_DAYS)

  print(
    render_terminal(
      usage_records,
      reset_records,
    )
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
