#!/usr/bin/env python3

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import codex_limits


class PercentBarTest(unittest.TestCase):
  def test_treats_values_at_or_below_one_as_percentages(self) -> None:
    self.assertEqual(codex_limits.percent_bar(0.5), "[--------------------] 0.5%")
    self.assertEqual(codex_limits.percent_bar(1.0), "[--------------------] 1%")

  def test_normalizes_reset_credit_against_full_period(self) -> None:
    with patch.object(codex_limits, "expiry_days_left", return_value=0.125):
      record = codex_limits.normalize_reset_record({"expires_at": "unused"}, reset_full_days=30)

    self.assertEqual(record["time_left_bar"], "[--------------------] 0.4%")


class ResetTimeLeftTest(unittest.TestCase):
  def test_displays_minutes_below_one_hour(self) -> None:
    self.assertEqual(codex_limits.display_days_left(30 / 1440), "30m")
    self.assertEqual(codex_limits.display_days_left(59.9 / 1440), "59m")

  def test_keeps_hours_at_one_hour(self) -> None:
    self.assertEqual(codex_limits.display_days_left(1 / 24), "1h")

  def test_uses_at_least_one_minute_before_expiry(self) -> None:
    self.assertEqual(codex_limits.display_days_left(1 / 86400), "1m")
    self.assertEqual(codex_limits.display_days_left(0), "expired")


class WeeklyActiveTimeTest(unittest.TestCase):
  def setUp(self) -> None:
    self.jst = ZoneInfo("Asia/Tokyo")

  def test_counts_only_time_between_nine_and_twenty(self) -> None:
    start = datetime(2026, 9, 1, 8, 0, tzinfo=self.jst)
    end = datetime(2026, 9, 1, 21, 0, tzinfo=self.jst)

    self.assertEqual(codex_limits.active_seconds_between(start, end), 11 * 60 * 60)

  def test_does_not_count_overnight_time(self) -> None:
    before_night = datetime(2026, 9, 1, 20, 0, tzinfo=self.jst)
    after_night = datetime(2026, 9, 2, 9, 0, tzinfo=self.jst)

    self.assertEqual(codex_limits.active_seconds_between(before_night, after_night), 0)

  def test_seven_day_window_contains_seventy_seven_active_hours(self) -> None:
    reset_at = datetime(2026, 9, 8, 15, 0, tzinfo=self.jst)
    window_start = reset_at - timedelta(days=7)

    self.assertEqual(codex_limits.active_seconds_between(window_start, reset_at), 77 * 60 * 60)

  def test_weekly_percentage_freezes_overnight(self) -> None:
    reset_at = datetime(2026, 9, 8, 15, 0, tzinfo=self.jst)
    at_twenty = datetime(2026, 9, 2, 20, 0, tzinfo=self.jst)
    next_morning = datetime(2026, 9, 3, 9, 0, tzinfo=self.jst)

    at_twenty_percent = codex_limits.window_remaining_percent(
      reset_at.timestamp(), codex_limits.WEEKLY_WINDOW_SECONDS, at_twenty
    )
    next_morning_percent = codex_limits.window_remaining_percent(
      reset_at.timestamp(), codex_limits.WEEKLY_WINDOW_SECONDS, next_morning
    )

    self.assertAlmostEqual(at_twenty_percent, next_morning_percent)

  def test_labels_weekly_time_as_active_time(self) -> None:
    records = [
      {
        "limit": "週間利用上限",
        "window_remaining_bar": "[==========----------] 50%",
        "reset": "09-08 15:00 JST",
      }
    ]

    self.assertEqual(
      codex_limits.window_time_left_rows(records),
      [("利用時間残り (09-20)", "[==========----------] 50%", "09-08 15:00 JST")],
    )


class ResetUrgencyTest(unittest.TestCase):
  def test_warns_below_one_day(self) -> None:
    self.assertEqual(codex_limits.reset_urgency(1), "")
    self.assertEqual(codex_limits.reset_urgency(23 / 24), "warning")

  def test_errors_below_two_hours(self) -> None:
    self.assertEqual(codex_limits.reset_urgency(2 / 24), "warning")
    self.assertEqual(codex_limits.reset_urgency(1.99 / 24), "error")
    self.assertEqual(codex_limits.reset_urgency(0), "error")

  def test_colors_the_entire_reset_row(self) -> None:
    record = {
      "status": "available",
      "time_left": "30m",
      "time_left_bar": "[--------------------] 0.1%",
      "issued": "07-01 00:00 JST",
      "expires": "07-31 00:00 JST",
      "redeemed": "no",
      "urgency": "error",
    }

    row = codex_limits.reset_rows([record])[0]

    self.assertTrue(row[0].startswith(codex_limits.ANSI_RED))
    self.assertTrue(row[-1].endswith(codex_limits.ANSI_RESET))


if __name__ == "__main__":
  unittest.main()
