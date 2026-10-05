import json

from rewind.cli import main
from tests.test_comparison import fixture


async def test_cli_comparison_labels_expected_return_and_changed_code(
    tmp_path, monkeypatch, capsys
):
    source, artifact = await fixture(tmp_path, monkeypatch)
    source.write_text(source.read_text().replace("return value", "return value + 1"))
    assert (
        main(
            [
                "compare",
                str(artifact),
                "--app",
                "comparison_fixture:factory",
                "--expected-return",
                "8",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "comparison"
    assert report["status"] == "matched"
    assert report["code_changed"] is True
    assert report["expected_source"] == "developer"
    assert main(["replay", str(artifact), "--app", "comparison_fixture:factory"]) == 2


async def test_cli_generates_explicit_regression_and_rejects_silent_oracle_override(
    tmp_path,
    monkeypatch,
    capsys,
):
    _, artifact = await fixture(tmp_path, monkeypatch)
    output = tmp_path / "test_regression.py"
    args = [
        "test",
        str(artifact),
        "--app",
        "comparison_fixture:factory",
        "--output",
        str(output),
        "--expected-return",
        "7",
    ]
    assert main(args) == 3
    assert not output.exists()
    capsys.readouterr()
    assert main(args + ["--compare-code"]) == 0
    assert json.loads(capsys.readouterr().out)["oracle"] == "developer outcome"
    assert "compare_file" in output.read_text()
    assert "report.matched" in output.read_text()


async def test_cli_resolves_snapshot_id_from_selected_store(tmp_path, monkeypatch, capsys):
    _, artifact = await fixture(tmp_path, monkeypatch)
    identifier = artifact.name.removesuffix(".rewind.json")
    assert main(["inspect", identifier, "--store", str(artifact.parent)]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == identifier


async def test_cli_rejects_malformed_expected_json_before_execution(tmp_path, monkeypatch):
    _, artifact = await fixture(tmp_path, monkeypatch)
    assert (
        main(
            [
                "compare",
                str(artifact),
                "--app",
                "comparison_fixture:factory",
                "--expected-return",
                '{"x":1,"x":2}',
            ]
        )
        == 3
    )
