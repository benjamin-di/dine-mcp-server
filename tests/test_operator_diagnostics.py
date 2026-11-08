import json

import pytest

from dine_mcp import cli


def test_operator_error_survives_critical_log_filter(monkeypatch, capsys):
    monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
    monkeypatch.setattr(cli.logs, "setup", lambda *args: None)

    def fail(args):
        raise ValueError("synthetic operator failure")

    monkeypatch.setattr(cli, "run", fail)
    with pytest.raises(SystemExit) as failure:
        cli.main(["people", "add"])
    assert failure.value.code == 1
    data = json.loads(capsys.readouterr().err)
    assert "synthetic operator failure" in data["msg"] and data["service"] == "dine"
