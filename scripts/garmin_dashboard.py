# /// script
# requires-python = ">=3.11"
# dependencies = ["garminconnect>=0.2.24"]
# ///
"""
Daily Garmin dashboard builder for ashifsheriff.art/garmin.

Reuses the same cached Garmin Connect session the `garmin_mcp` MCP server
already uses (~/.garminconnect), so no password lives in this script.
Fetches a curated snapshot of health/fitness data, renders it into a static
`garmin/index.html` (+ `garmin/data.json`) in this repo, then commits and
pushes just that directory so Netlify redeploys ashifsheriff.art.

Run manually:   uv run scripts/garmin_dashboard.py
Run by launchd: see ~/Library/LaunchAgents/com.ashifsheriff.garmin-dashboard.plist

The curated-field extraction below intentionally mirrors the parsing logic
in Taxuspt/garmin_mcp (health_wellness.py / training.py / weight_management.py /
activity_management.py) so this script's output matches what that MCP server
already surfaces.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from garminconnect import Garmin

REPO_ROOT = Path(__file__).resolve().parent.parent
GARMIN_DIR = REPO_ROOT / "garmin"
TOKEN_STORE = os.path.expanduser("~/.garminconnect")
LOG_PATH = os.path.expanduser("~/Library/Logs/garmin-dashboard.log")
STEPS_TREND_DAYS = 30
BATTERY_TREND_DAYS = 14
WEIGHT_LOOKBACK_DAYS = 180
ACTIVITY_LIMIT = 7

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("garmin-dashboard")


# --------------------------------------------------------------------------
# Data fetch — each curated the same way the garmin_mcp tools curate them.
# --------------------------------------------------------------------------

def _date_range(start: dt.date, end: dt.date) -> list[str]:
    n = (end - start).days
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(n + 1)]


def fetch_snapshot(garmin: Garmin, today: dt.date) -> dict:
    today_s = today.isoformat()
    data: dict = {"date": today_s, "generated_at": dt.datetime.now().isoformat()}

    try:
        data["full_name"] = garmin.get_full_name()
    except Exception as e:
        log.warning("get_full_name failed: %s", e)
        data["full_name"] = None

    try:
        data["summary"] = garmin.get_user_summary(today_s)
    except Exception as e:
        log.warning("get_user_summary failed: %s", e)
        data["summary"] = {}

    try:
        data["sleep"] = _curate_sleep(garmin.get_sleep_data(today_s))
    except Exception as e:
        log.warning("get_sleep_data failed: %s", e)
        data["sleep"] = {}

    try:
        data["hrv"] = _curate_hrv(garmin.get_hrv_data(today_s))
    except Exception as e:
        log.warning("get_hrv_data failed: %s", e)
        data["hrv"] = {}

    try:
        readiness_list = garmin.get_training_readiness(today_s) or []
        data["readiness"] = _curate_readiness(readiness_list[0]) if readiness_list else {}
    except Exception as e:
        log.warning("get_training_readiness failed: %s", e)
        data["readiness"] = {}

    try:
        data["training_status"] = _curate_training_status(garmin.get_training_status(today_s))
    except Exception as e:
        log.warning("get_training_status failed: %s", e)
        data["training_status"] = {}

    try:
        start_date = today - dt.timedelta(days=STEPS_TREND_DAYS - 1)
        raw_steps = garmin.get_daily_steps(start_date.isoformat(), today_s) or []
        by_date = {d.get("calendarDate"): d for d in raw_steps}
        data["steps_trend"] = [
            {"calendarDate": iso, "totalSteps": (by_date.get(iso) or {}).get("totalSteps"),
             "stepGoal": (by_date.get(iso) or {}).get("stepGoal")}
            for iso in _date_range(start_date, today)
        ]
    except Exception as e:
        log.warning("get_daily_steps failed: %s", e)
        data["steps_trend"] = []

    try:
        start_date = today - dt.timedelta(days=BATTERY_TREND_DAYS - 1)
        raw_battery = garmin.get_body_battery(start_date.isoformat(), today_s) or []
        by_date = {d.get("date"): d for d in raw_battery}
        data["battery_trend"] = [
            {"date": iso, "charged": (by_date.get(iso) or {}).get("charged")}
            for iso in _date_range(start_date, today)
        ]
    except Exception as e:
        log.warning("get_body_battery failed: %s", e)
        data["battery_trend"] = []

    try:
        start = (today - dt.timedelta(days=WEIGHT_LOOKBACK_DAYS)).isoformat()
        data["weigh_ins"] = _curate_weigh_ins(garmin.get_weigh_ins(start, today_s))
    except Exception as e:
        log.warning("get_weigh_ins failed: %s", e)
        data["weigh_ins"] = []

    try:
        raw_activities = garmin.get_activities(0, ACTIVITY_LIMIT) or []
        data["activities"] = [_curate_activity(a) for a in raw_activities]
    except Exception as e:
        log.warning("get_activities failed: %s", e)
        data["activities"] = []

    return data


def _curate_sleep(sleep_data: dict | None) -> dict:
    if not sleep_data:
        return {}
    daily = sleep_data.get("dailySleepDTO") or {}
    out = {
        "sleep_seconds": daily.get("sleepTimeSeconds"),
        "sleep_score": (daily.get("sleepScores") or {}).get("overall", {}).get("value"),
        "sleep_score_qualifier": (daily.get("sleepScores") or {}).get("overall", {}).get("qualifierKey"),
        "deep_sleep_seconds": daily.get("deepSleepSeconds") or 0,
        "light_sleep_seconds": daily.get("lightSleepSeconds") or 0,
        "rem_sleep_seconds": daily.get("remSleepSeconds") or 0,
        "awake_seconds": daily.get("awakeSleepSeconds") or 0,
        "avg_sleep_stress": daily.get("avgSleepStress"),
    }
    spo2 = sleep_data.get("wellnessSpO2SleepSummaryDTO") or {}
    out["avg_spo2_percent"] = spo2.get("averageSpo2")
    return out


def _curate_hrv(hrv_data: dict | None) -> dict:
    if not hrv_data:
        return {}
    summary = hrv_data.get("hrvSummary") or {}
    baseline = summary.get("baseline") or {}
    return {
        "last_night_avg_hrv_ms": summary.get("lastNightAvg"),
        "weekly_avg_hrv_ms": summary.get("weeklyAvg"),
        "baseline_balanced_low_ms": baseline.get("balancedLow"),
        "baseline_balanced_upper_ms": baseline.get("balancedUpper"),
        "status": summary.get("status"),
    }


def _curate_readiness(r: dict) -> dict:
    return {
        "score": r.get("score"),
        "level": r.get("level"),
        "feedback": r.get("feedbackShort"),
        "sleep_score": r.get("sleepScore"),
        "recovery_factor_feedback": r.get("recoveryTimeFactorFeedback"),
        "training_load_feedback": r.get("acwrFactorFeedback"),
        "hrv_weekly_avg": r.get("hrvWeeklyAverage"),
    }


def _curate_training_status(status: dict | None) -> dict:
    if not status:
        return {}
    recent_status = status.get("mostRecentTrainingStatus") or {}
    latest_data = recent_status.get("latestTrainingStatusData") or {}
    device_data = {}
    for _device_id, d in latest_data.items():
        device_data = d
        break
    acwr = device_data.get("acuteTrainingLoadDTO") or {}
    vo2 = (status.get("mostRecentVO2Max") or {}).get("generic") or {}
    return {
        "training_status_feedback": device_data.get("trainingStatusFeedbackPhrase"),
        "chronic_load": acwr.get("dailyTrainingLoadChronic"),
        "acute_load": acwr.get("dailyTrainingLoadAcute"),
        "optimal_chronic_load_min": acwr.get("minTrainingLoadChronic"),
        "optimal_chronic_load_max": acwr.get("maxTrainingLoadChronic"),
        "vo2_max": vo2.get("vo2MaxValue"),
    }


def _curate_weigh_ins(data: dict | None) -> list[dict]:
    if not data:
        return []
    out = []
    for day in data.get("dailyWeightSummaries") or []:
        for w in day.get("allWeightMetrics") or []:
            weight = w.get("weight")
            if weight is None:
                continue
            out.append({"date": w.get("calendarDate"), "weight_kg": round(weight / 1000, 1)})
    out.sort(key=lambda x: x.get("date") or "")
    return out


def _curate_activity(a: dict) -> dict:
    return {
        "name": a.get("activityName"),
        "type": (a.get("activityType") or {}).get("typeKey"),
        "start_time": a.get("startTimeLocal"),
        "distance_meters": a.get("distance"),
        "duration_seconds": a.get("duration"),
        "calories": a.get("calories"),
        "avg_hr_bpm": a.get("averageHR"),
    }


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def fmt_int(v) -> str:
    return f"{int(round(v)):,}" if v is not None else "—"


def fmt_hm(seconds) -> str:
    if not seconds:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"


def fmt_km(meters) -> str:
    if not meters:
        return "—"
    return f"{meters / 1000:.1f} km"


def fmt_pace(meters, seconds) -> str:
    if not meters or not seconds:
        return "—"
    km = meters / 1000
    if km <= 0:
        return "—"
    pace_min = (seconds / 60) / km
    m, s = divmod(int(round(pace_min * 60)), 60)
    return f"{m}:{s:02d} /km"


def fmt_activity_type(type_key) -> str:
    if not type_key:
        return "Activity"
    return type_key.replace("_", " ").title()


def fmt_date_human(date_s) -> str:
    try:
        d = dt.date.fromisoformat(date_s)
        return d.strftime("%b %-d")
    except Exception:
        return date_s or ""


# --------------------------------------------------------------------------
# Tiny inline-SVG chart helpers (per the dataviz skill: thin marks, hairline
# gridlines, 4px rounded bar tops, no dual axes, direct end-labels).
# --------------------------------------------------------------------------

def svg_bar_chart(values: list[tuple[str, float | None]], goal: float | None, height: int = 140) -> str:
    """values: list of (date_label, value). Single-series bar chart with an
    optional goal reference hairline. A None value renders as a muted
    no-data tick rather than a silent gap or a misleading zero bar."""
    n = len(values)
    if n == 0:
        return '<p class="no-data">No data for this range.</p>'
    width = max(n * 20, 320)
    real_vals = [v for _, v in values if v is not None]
    max_val = max(real_vals + [goal or 0]) * 1.15 if real_vals else 1
    bar_w = min(16, (width / n) - 4)
    gap = (width / n) - bar_w
    bars = []
    for i, (_label, v) in enumerate(values):
        x = i * (bar_w + gap) + gap / 2
        if v is None:
            bars.append(
                f'<rect x="{x:.1f}" y="{height-22:.1f}" width="{bar_w:.1f}" height="2" '
                f'rx="1" class="bar-nodata"><title>{html.escape(_label)}: no data</title></rect>'
            )
            continue
        h = (v / max_val) * (height - 24)
        y = height - 20 - h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h,1):.1f}" '
            f'rx="4" class="bar"><title>{html.escape(_label)}: {fmt_int(v)}</title></rect>'
        )
    goal_line = ""
    if goal:
        gy = height - 20 - (goal / max_val) * (height - 24)
        goal_line = (
            f'<line x1="0" y1="{gy:.1f}" x2="{width}" y2="{gy:.1f}" class="goal-line"/>'
            f'<text x="{width}" y="{gy - 4:.1f}" class="goal-label" text-anchor="end">goal {fmt_int(goal)}</text>'
        )
    baseline = f'<line x1="0" y1="{height-20}" x2="{width}" y2="{height-20}" class="baseline"/>'
    first_label = values[0][0]
    last_label = values[-1][0]
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" preserveAspectRatio="none" role="img" '
        f'aria-label="Daily trend chart">'
        f"{baseline}{''.join(bars)}{goal_line}"
        f'<text x="2" y="{height-4}" class="axis-label">{html.escape(first_label)}</text>'
        f'<text x="{width-2}" y="{height-4}" class="axis-label" text-anchor="end">{html.escape(last_label)}</text>'
        f"</svg>"
    )


def svg_line_chart(values: list[tuple[str, float | None]], height: int = 100, color_var: str = "--series-1") -> str:
    """values: list of (date_label, value | None). Gaps (None) break the line
    into separate segments rather than interpolating across missing days."""
    n = len(values)
    real = [(i, v) for i, (_l, v) in enumerate(values) if v is not None]
    if len(real) == 0:
        return '<p class="no-data">No data for this range.</p>'
    if len(real) == 1:
        return f'<p class="no-data">Only one data point ({real[0][1]:g}) — need more history for a trend.</p>'
    width = max(n * 18, 320)
    lo = min(v for _, v in real)
    hi = max(v for _, v in real)
    span = (hi - lo) or 1
    pad = 16

    def point(i, v):
        x = (i / (n - 1)) * (width - 2 * pad) + pad if n > 1 else pad
        y = height - pad - ((v - lo) / span) * (height - 2 * pad)
        return x, y

    # Group into contiguous runs (a None breaks the run) so the line never
    # bridges a gap in the data.
    paths = []
    dots = []
    run: list[tuple[int, float]] = []

    def flush_run():
        if len(run) >= 2:
            pts = [point(i, v) for i, v in run]
            paths.append("M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts))
        elif len(run) == 1:
            x, y = point(*run[0])
            dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" class="dot" style="opacity:1"/>')

    for i, (_label, v) in enumerate(values):
        if v is None:
            flush_run()
            run = []
        else:
            run.append((i, v))
    flush_run()

    last_i, last_v = real[-1]
    last_x, last_y = point(last_i, last_v)
    hover_dots = "".join(
        f'<circle cx="{point(i, v)[0]:.1f}" cy="{point(i, v)[1]:.1f}" r="2.5" class="dot">'
        f"<title>{html.escape(values[i][0])}: {v:g}</title></circle>"
        for i, v in real
    )
    lines = "".join(f'<path d="{p}" class="line"/>' for p in paths)
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" preserveAspectRatio="none" role="img" '
        f'aria-label="Trend chart" style="--stroke:var({color_var})">'
        f"{lines}{''.join(dots)}{hover_dots}"
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" class="end-dot"/>'
        f'<text x="{last_x:.1f}" y="{max(last_y-10,10):.1f}" class="end-label" text-anchor="end">{last_v:g}</text>'
        f"</svg>"
    )


def svg_sleep_stages(sleep: dict) -> str:
    segs = [
        ("Deep", sleep.get("deep_sleep_seconds") or 0, "var(--series-1)"),
        ("Light", sleep.get("light_sleep_seconds") or 0, "var(--series-3)"),
        ("REM", sleep.get("rem_sleep_seconds") or 0, "var(--series-7)"),
        ("Awake", sleep.get("awake_seconds") or 0, "var(--muted-ink)"),
    ]
    total = sum(s[1] for s in segs) or 1
    width = 600
    x = 0
    rects = []
    legend = []
    for name, secs, color in segs:
        w = (secs / total) * width
        if w > 1:
            rects.append(
                f'<rect x="{x:.1f}" y="0" width="{max(w-2,0):.1f}" height="28" rx="4" '
                f'fill="{color}"><title>{name}: {fmt_hm(secs)}</title></rect>'
            )
        x += w
        legend.append(
            f'<span class="legend-item"><i style="background:{color}"></i>{name} · {fmt_hm(secs)}</span>'
        )
    return (
        f'<svg viewBox="0 0 {width} 28" class="chart stages" preserveAspectRatio="none" role="img" '
        f'aria-label="Sleep stages">{"".join(rects)}</svg>'
        f'<div class="legend">{"".join(legend)}</div>'
    )


# --------------------------------------------------------------------------
# HTML render
# --------------------------------------------------------------------------

def stat_tile(label: str, value: str, sub: str = "", status: str | None = None) -> str:
    status_cls = f" status-{status}" if status else ""
    sub_html = f'<div class="tile-sub">{html.escape(sub)}</div>' if sub else ""
    return (
        f'<div class="tile{status_cls}">'
        f'<div class="tile-label">{html.escape(label)}</div>'
        f'<div class="tile-value">{value}</div>'
        f"{sub_html}</div>"
    )


def readiness_status(level: str | None) -> str | None:
    return {"HIGH": "good", "MODERATE": "warning", "LOW": "serious"}.get((level or "").upper())


def render_html(data: dict) -> str:
    summary = data.get("summary") or {}
    sleep = data.get("sleep") or {}
    hrv = data.get("hrv") or {}
    readiness = data.get("readiness") or {}
    tstatus = data.get("training_status") or {}
    steps_trend = data.get("steps_trend") or []
    battery_trend = data.get("battery_trend") or []
    weigh_ins = data.get("weigh_ins") or []
    activities = data.get("activities") or []

    name = data.get("full_name") or "Ashif"
    today_label = dt.date.fromisoformat(data["date"]).strftime("%A, %B %-d, %Y")
    generated_label = dt.datetime.fromisoformat(data["generated_at"]).strftime("%-I:%M %p")

    # --- headline tiles ---
    steps = summary.get("totalSteps")
    step_goal = summary.get("dailyStepGoal")
    steps_tile = stat_tile(
        "Steps today", fmt_int(steps),
        f"of {fmt_int(step_goal)} goal" if step_goal else "",
    )

    sleep_score = sleep.get("sleep_score")
    sleep_tile = stat_tile(
        "Sleep last night", f"{fmt_int(sleep_score)}" if sleep_score is not None else "—",
        f"{fmt_hm(sleep.get('sleep_seconds'))} · {sleep.get('sleep_score_qualifier','').title()}".strip(" ·"),
    )

    rhr = summary.get("restingHeartRate")
    rhr7 = summary.get("lastSevenDaysAvgRestingHeartRate")
    rhr_tile = stat_tile(
        "Resting heart rate", f"{fmt_int(rhr)} bpm" if rhr else "—",
        f"7-day avg {fmt_int(rhr7)} bpm" if rhr7 else "",
    )

    bb_now = summary.get("bodyBatteryMostRecentValue")
    bb_hi = summary.get("bodyBatteryHighestValue")
    bb_lo = summary.get("bodyBatteryLowestValue")
    battery_tile = stat_tile(
        "Body battery", f"{fmt_int(bb_now)}" if bb_now is not None else "—",
        f"range {fmt_int(bb_lo)}–{fmt_int(bb_hi)} today",
    )

    stress_avg = summary.get("averageStressLevel")
    stress_tile = stat_tile(
        "Stress (avg today)", f"{fmt_int(stress_avg)}" if stress_avg is not None else "—",
        f"peak {fmt_int(summary.get('maxStressLevel'))}",
    )

    r_score = readiness.get("score")
    r_status = readiness_status(readiness.get("level"))
    readiness_tile = stat_tile(
        "Training readiness", f"{fmt_int(r_score)}" if r_score is not None else "—",
        (readiness.get("feedback") or "").replace("_", " ").title(),
        status=r_status,
    )

    hrv_val = hrv.get("last_night_avg_hrv_ms")
    hrv_tile = stat_tile(
        "Overnight HRV", f"{fmt_int(hrv_val)} ms" if hrv_val else "—",
        f"7-day avg {fmt_int(hrv.get('weekly_avg_hrv_ms'))} ms" if hrv.get("weekly_avg_hrv_ms") else "",
    )

    vo2 = tstatus.get("vo2_max")
    vo2_tile = stat_tile(
        "VO2 max", f"{vo2:g}" if vo2 else "—",
        (tstatus.get("training_status_feedback") or "").replace("_", " ").title(),
    )

    tiles_html = "".join(
        [steps_tile, sleep_tile, rhr_tile, battery_tile, stress_tile, readiness_tile, hrv_tile, vo2_tile]
    )

    # --- charts ---
    steps_labels = [(fmt_date_human(d.get("calendarDate")), d.get("totalSteps")) for d in steps_trend]
    latest_goal = next((d.get("stepGoal") for d in reversed(steps_trend) if d.get("stepGoal")), None)
    steps_chart = svg_bar_chart(steps_labels, latest_goal)

    battery_labels = [(fmt_date_human(d.get("date")), d.get("charged")) for d in battery_trend]
    battery_chart = svg_line_chart(battery_labels)

    sleep_stages_chart = svg_sleep_stages(sleep) if sleep.get("sleep_seconds") else '<p class="no-data">No sleep data.</p>'

    weight_html = ""
    if weigh_ins:
        if len(weigh_ins) >= 2:
            w_series = [(fmt_date_human(w["date"]), w["weight_kg"]) for w in weigh_ins]
            weight_chart = svg_line_chart(w_series, color_var="--series-2")
            weight_html = f'<section class="card"><h2>Weight</h2>{weight_chart}</section>'
        else:
            w = weigh_ins[-1]
            weight_html = (
                '<section class="card"><h2>Weight</h2>'
                + stat_tile("Last logged", f"{w['weight_kg']:g} kg", fmt_date_human(w["date"]))
                + "</section>"
            )

    # --- activities table ---
    if activities:
        rows = []
        for a in activities:
            date_s = (a.get("start_time") or "")[:10]
            type_label = fmt_activity_type(a.get("type"))
            dist = fmt_km(a.get("distance_meters"))
            dur = fmt_hm(a.get("duration_seconds"))
            pace = fmt_pace(a.get("distance_meters"), a.get("duration_seconds")) if a.get("type") == "running" else "—"
            hr = f"{fmt_int(a.get('avg_hr_bpm'))} bpm" if a.get("avg_hr_bpm") else "—"
            cal = fmt_int(a.get("calories")) if a.get("calories") else "—"
            rows.append(
                "<tr>"
                f"<td>{html.escape(fmt_date_human(date_s))}</td>"
                f"<td>{html.escape(a.get('name') or type_label)}</td>"
                f"<td>{html.escape(type_label)}</td>"
                f"<td>{dist}</td><td>{dur}</td><td>{pace}</td><td>{hr}</td><td>{cal}</td>"
                "</tr>"
            )
        activities_html = (
            '<section class="card"><h2>Recent activities</h2>'
            '<div class="table-wrap"><table><thead><tr>'
            "<th>Date</th><th>Activity</th><th>Type</th><th>Distance</th>"
            "<th>Duration</th><th>Pace</th><th>Avg HR</th><th>Calories</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
        )
    else:
        activities_html = '<section class="card"><h2>Recent activities</h2><p class="no-data">No recent activities.</p></section>'

    return TEMPLATE.format(
        name=html.escape(name.strip() or "Ashif"),
        today_label=today_label,
        generated_label=generated_label,
        tiles=tiles_html,
        steps_chart=steps_chart,
        battery_chart=battery_chart,
        sleep_stages_chart=sleep_stages_chart,
        weight_html=weight_html,
        activities_html=activities_html,
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Garmin — {name}</title>
<meta name="robots" content="noindex">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%8C%9A%EF%B8%8F%3C/text%3E%3C/svg%3E">
<style>
  :root {{
    color-scheme: light;
    --page-bg: #f9f9f7;
    --surface-1: #fcfcfb;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --muted-ink: #898781;
    --gridline: #e1e0d9;
    --baseline: #c3c2b7;
    --border: rgba(11,11,11,0.10);
    --series-1: #2a78d6;
    --series-2: #eb6834;
    --series-3: #1baf7a;
    --series-7: #4a3aa7;
    --good: #0ca30c;
    --warning: #fab219;
    --serious: #ec835a;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      color-scheme: dark;
      --page-bg: #0d0d0d;
      --surface-1: #1a1a19;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --muted-ink: #898781;
      --gridline: #2c2c2a;
      --baseline: #383835;
      --border: rgba(255,255,255,0.10);
      --series-1: #3987e5;
      --series-2: #d95926;
      --series-3: #199e70;
      --series-7: #9085e9;
      --good: #0ca30c;
      --warning: #fab219;
      --serious: #ec835a;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--page-bg);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 40px 20px 80px; }}
  header {{ margin-bottom: 28px; }}
  header h1 {{ font-size: 22px; font-weight: 600; margin: 0 0 4px; }}
  header p {{ margin: 0; color: var(--text-secondary); font-size: 14px; }}
  .tiles {{
    display: grid;
    grid-template-columns: repeat(4, minmax(150px, 1fr));
    gap: 2px;
    background: var(--border);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 28px;
  }}
  @media (max-width: 640px) {{ .tiles {{ grid-template-columns: repeat(2, 1fr); }} }}
  .tile {{ background: var(--surface-1); padding: 16px; }}
  .tile-label {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 6px; }}
  .tile-value {{ font-size: 26px; font-weight: 600; line-height: 1.1; }}
  .tile-sub {{ font-size: 12px; color: var(--muted-ink); margin-top: 4px; }}
  .tile.status-good .tile-value {{ color: var(--good); }}
  .tile.status-warning .tile-value {{ color: var(--warning); }}
  .tile.status-serious .tile-value {{ color: var(--serious); }}
  .card {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
  }}
  .card h2 {{ font-size: 15px; font-weight: 600; margin: 0 0 14px; }}
  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media (max-width: 640px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
  .chart {{ width: 100%; height: auto; display: block; }}
  .chart .bar {{ fill: var(--series-1); }}
  .chart .bar-nodata {{ fill: var(--gridline); }}
  .chart .baseline {{ stroke: var(--baseline); stroke-width: 1; }}
  .chart .goal-line {{ stroke: var(--muted-ink); stroke-width: 1; }}
  .chart .goal-label {{ fill: var(--muted-ink); font-size: 9px; }}
  .chart .axis-label {{ fill: var(--muted-ink); font-size: 9px; }}
  .chart .line {{ fill: none; stroke: var(--stroke, var(--series-1)); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }}
  .chart .dot {{ fill: var(--stroke, var(--series-1)); opacity: 0; }}
  .chart .dot:hover {{ opacity: 1; }}
  .chart .end-dot {{ fill: var(--stroke, var(--series-1)); stroke: var(--surface-1); stroke-width: 2; }}
  .chart .end-label {{ fill: var(--text-primary); font-size: 11px; font-weight: 600; }}
  .no-data {{ color: var(--muted-ink); font-size: 13px; margin: 0; }}
  .legend {{ margin-top: 10px; display: flex; flex-wrap: wrap; gap: 14px; }}
  .legend-item {{ font-size: 12px; color: var(--text-secondary); display: inline-flex; align-items: center; gap: 6px; }}
  .legend-item i {{ width: 8px; height: 8px; border-radius: 2px; display: inline-block; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  .table-wrap {{ overflow-x: auto; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--gridline); white-space: nowrap; }}
  th {{ color: var(--text-secondary); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: 0.03em; }}
  footer {{ margin-top: 32px; color: var(--muted-ink); font-size: 12px; text-align: center; }}
  footer a {{ color: inherit; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{name}'s Garmin dashboard</h1>
    <p>{today_label} · updated {generated_label}, refreshes daily</p>
  </header>

  <div class="tiles">{tiles}</div>

  <div class="grid-2">
    <section class="card">
      <h2>Steps — last 30 days</h2>
      {steps_chart}
    </section>
    <section class="card">
      <h2>Body battery charged — last 14 days</h2>
      {battery_chart}
    </section>
  </div>

  <section class="card">
    <h2>Sleep stages — last night</h2>
    {sleep_stages_chart}
  </section>

  {weight_html}

  {activities_html}

  <footer>
    Built automatically from Garmin Connect data. <a href="/">ashifsheriff.art</a>
  </footer>
</div>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Git deploy
# --------------------------------------------------------------------------

def deploy(commit_message: str) -> None:
    def run(*args):
        return subprocess.run(args, cwd=REPO_ROOT, check=True, capture_output=True, text=True)

    run("git", "fetch", "origin", "main")
    # --autostash: this repo often has unrelated in-progress edits sitting in
    # the working tree from other projects; autostash sets them aside for the
    # rebase and restores them after, so this script never has to know about
    # or touch work that isn't its own.
    run("git", "pull", "--rebase", "--autostash", "origin", "main")
    run("git", "add", "garmin/")
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT
    )
    if status.returncode == 0:
        log.info("No changes to garmin/ — skipping commit.")
        return
    run("git", "commit", "-m", commit_message)
    run("git", "push", "origin", "main")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    try:
        garmin = Garmin()
        garmin.login(TOKEN_STORE)
    except Exception as e:
        log.error("Garmin login failed: %s", e)
        print(f"Garmin login failed: {e}", file=sys.stderr)
        return 1

    today = dt.date.today()
    try:
        data = fetch_snapshot(garmin, today)
    except Exception as e:
        log.error("Fetching Garmin data failed: %s", e)
        print(f"Fetching Garmin data failed: {e}", file=sys.stderr)
        return 1

    GARMIN_DIR.mkdir(exist_ok=True)
    (GARMIN_DIR / "data.json").write_text(json.dumps(data, indent=2, default=str))
    (GARMIN_DIR / "index.html").write_text(render_html(data))
    log.info("Rendered dashboard for %s", today.isoformat())

    try:
        deploy(f"Update Garmin dashboard - {today.isoformat()}")
        log.info("Deployed successfully.")
    except subprocess.CalledProcessError as e:
        log.error("Git deploy failed: %s\nstdout=%s\nstderr=%s", e, e.stdout, e.stderr)
        print(f"Git deploy failed: {e.stderr}", file=sys.stderr)
        return 1

    print(f"Garmin dashboard updated for {today.isoformat()}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
