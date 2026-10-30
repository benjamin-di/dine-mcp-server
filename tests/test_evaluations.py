"""The evaluation world and runner must preserve the distinctions their prompts test."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import soon

ROOT = Path(__file__).resolve().parents[1]


def load_eval(name):
    spec = importlib.util.spec_from_file_location("dine_eval_" + name, ROOT / "evals" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def evaluation_app(make_app):
    catalog = load_eval("catalog")
    app = make_app(catalog.places(), catalog.providers())
    app.service.resolver.curated = catalog.curated()
    return app


@pytest.mark.parametrize("name, country, stamp", [("青嵐亭 ひかり", "JP", "19:15"), ("星澄小館", "SG", "20:15")])
async def test_non_latin_eval_names_reach_the_correct_provider(evaluation_app, name, country, stamp):
    service = evaluation_app.service
    found = await service.find_venue("local", name=name, country=country)
    assert found["venues"][0]["name"] == name
    local_day = datetime.now(ZoneInfo("Asia/Tokyo" if country == "JP" else "Asia/Singapore")).date() + timedelta(days=1)
    result = await service.check_availability("local", [found["venues"][0]["venue_id"]], local_day.isoformat())
    assert result["results"][0]["slots"][0]["start"][11:16] == stamp


async def test_exact_same_name_in_two_countries_remains_ambiguous(evaluation_app):
    found = await evaluation_app.service.find_venue("local", name="Cedar Room")
    assert found["venues"] == [] and len(found["candidates"]) == 2
    assert {c["address"] for c in found["candidates"]} == {"11 Example Lane, Singapore", "12 Example Lane, London"}


async def test_messaging_only_eval_returns_contact_without_fake_availability(evaluation_app):
    service = evaluation_app.service
    found = await service.find_venue("local", name="Paper Lantern Studio", country="SG")
    result = (await service.check_availability("local", [found["venues"][0]["venue_id"]], soon(1).isoformat()))[
        "results"
    ][0]
    assert result["status"] == "contact_only" and result["slots"] == []
    assert "WhatsApp" in result["contact"]
    assert found["venues"][0]["channels"][0]["url"] == "https://wa.me/6500000000"


async def test_partial_pushback_eval_has_observed_slots_and_unknown_later_dates(evaluation_app):
    service = evaluation_app.service
    found = await service.find_venue("local", name="Batchwork Grill", country="SG")
    result = (
        await service.check_availability(
            "local", [found["venues"][0]["venue_id"]], soon(1).isoformat(), soon(3).isoformat()
        )
    )["results"][0]
    assert result["status"] == "rate_limited" and result["slots"][0]["start"][11:16] == "19:00"
    assert result["coverage"] == {
        "dates_checked": [soon(1).isoformat()],
        "dates_not_checked": [soon(2).isoformat(), soon(3).isoformat()],
    }


async def test_overnight_eval_preserves_tokyo_dates_and_offsets(evaluation_app):
    service = evaluation_app.service
    found = await service.find_venue("local", name="Late Lantern", country="JP")
    first = datetime.now(ZoneInfo("Asia/Tokyo")).date() + timedelta(days=1)
    result = (
        await service.check_availability(
            "local", [found["venues"][0]["venue_id"]], first.isoformat(), time_from="23:00", time_to="01:00"
        )
    )["results"][0]
    assert [s["start"] for s in result["slots"]] == [
        first.isoformat() + "T23:45:00+09:00",
        (first + timedelta(days=1)).isoformat() + "T00:30:00+09:00",
    ]


def test_eval_runner_reports_timeout_and_cleans_up_configuration(monkeypatch):
    runner = load_eval("run")
    configs = []

    def timeout(command, **kwargs):
        config = Path(command[command.index("--mcp-config") + 1])
        configs.append(config)
        assert config.exists()
        raise subprocess.TimeoutExpired(command, 300)

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    result = runner.run_once({"id": "synthetic", "prompt": "synthetic"}, "synthetic-model")
    assert result["ok"] is False and result["error"] == "model_timeout"
    assert not configs[0].exists()


def test_eval_runner_rejects_nonzero_exit_even_with_matching_answer(monkeypatch):
    runner = load_eval("run")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 1, json.dumps({"result": "link"}), ""),
    )
    assert not runner.run_once({"id": "synthetic", "prompt": "synthetic", "expect": ["link"]}, "synthetic-model")["ok"]


def test_eval_command_returns_failure_and_keeps_a_report(monkeypatch, tmp_path):
    runner = load_eval("run")
    monkeypatch.setattr(runner, "HERE", tmp_path)
    (tmp_path / "tasks.toml").write_text('[[task]]\nid="synthetic"\nprompt="synthetic"\n')
    monkeypatch.setattr(runner, "run_once", lambda *args: {"id": "synthetic", "ok": False})
    monkeypatch.setattr(sys, "argv", ["run.py", "--model", "synthetic-model", "--runs", "1"])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 1
    report = json.loads(next((tmp_path / "results").glob("*.json")).read_text())
    assert report["success_rate"] == 0 and report["runs"] == 1


@pytest.mark.parametrize(
    "task_id, answer, expected",
    [
        ("exact-name-two-countries", "Which country is Cedar Room in?", True),
        ("exact-name-two-countries", "I assumed Singapore; let me know if you meant London.", False),
        (
            "ambiguous-resolved",
            "Amberley in Sydney: +61 2 5550 0505. I excluded Club Street in Singapore.",
            True,
        ),
        ("ambiguous-resolved", "Amberley Kitchen on Club Street, Singapore: call +65 6550 0404.", False),
    ],
)
def test_clarification_verifiers_distinguish_selection_from_rejected_candidates(task_id, answer, expected):
    tasks = tomllib.loads((ROOT / "evals/tasks.toml").read_text())["task"]
    task = next(t for t in tasks if t["id"] == task_id)
    passed = all(re.search(p, answer, re.I | re.S) for p in task["expect"]) and not any(
        re.search(p, answer, re.I | re.S) for p in task["forbid"]
    )
    assert passed is expected


def test_eval_world_includes_current_local_day_across_time_zone_midnight():
    catalog = load_eval("catalog")
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    slots = catalog.providers()["tablecheck"].venues
    assert slots["synthetic-hikari"][today] == ["19:15"]
    assert slots["synthetic-late-lantern"][today] == ["00:30", "23:45"]
