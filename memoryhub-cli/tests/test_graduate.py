"""Tests for the memoryhub graduate command."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from memoryhub_cli.main import app

runner = CliRunner()

GRADUATED_MEMORY = MagicMock()
GRADUATED_MEMORY.id = "abc-123-graduated"
GRADUATED_MEMORY.weight = 0.85
GRADUATED_MEMORY.content_type = "knowledge"
GRADUATED_MEMORY.scope = "project"
GRADUATED_MEMORY.version = 1
GRADUATED_MEMORY.model_dump.return_value = {
    "id": "abc-123-graduated",
    "weight": 0.85,
    "content_type": "knowledge",
    "scope": "project",
    "version": 1,
    "content": "Graduated knowledge memory",
}


def _mock_client(graduate_return=None):
    """Build a mock MemoryHubClient with graduate() wired up."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.graduate = AsyncMock(return_value=graduate_return or GRADUATED_MEMORY)
    return client


def _patch_client(client):
    """Patch both _get_client (returns mock) and env (provides API key + URL)."""
    return patch("memoryhub_cli.main._get_client", return_value=client)


def _patch_project_id(project_id=None):
    return patch("memoryhub_cli.main._get_project_id_default", return_value=project_id)


class TestGraduate:
    def test_table_output(self):
        client = _mock_client()
        with _patch_client(client), _patch_project_id():
            result = runner.invoke(app, ["graduate", "mem-uuid-123"])

        assert result.exit_code == 0, result.output
        assert "Graduated:" in result.output
        assert "abc-123-graduated" in result.output
        assert "knowledge" in result.output
        assert "0.85" in result.output
        client.graduate.assert_awaited_once_with(
            "mem-uuid-123",
            evidence=None,
            reviewer_note=None,
            project_id=None,
        )

    def test_with_evidence_and_reviewer_note(self):
        client = _mock_client()
        with _patch_client(client), _patch_project_id("my-project"):
            result = runner.invoke(app, [
                "graduate", "mem-uuid-456",
                "--evidence", "Observed 5 times in production",
                "--reviewer-note", "Consistent pattern",
                "--project-id", "custom-proj",
            ])

        assert result.exit_code == 0, result.output
        assert "Graduated:" in result.output
        client.graduate.assert_awaited_once_with(
            "mem-uuid-456",
            evidence="Observed 5 times in production",
            reviewer_note="Consistent pattern",
            project_id="custom-proj",
        )

    def test_json_output(self):
        client = _mock_client()
        with _patch_client(client), _patch_project_id():
            result = runner.invoke(app, [
                "graduate", "mem-uuid-789", "--output", "json",
            ])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["status"] == "ok"
        assert parsed["data"]["id"] == "abc-123-graduated"
        assert parsed["data"]["content_type"] == "knowledge"

    def test_quiet_output(self):
        client = _mock_client()
        with _patch_client(client), _patch_project_id():
            result = runner.invoke(app, [
                "graduate", "mem-uuid-000", "--output", "quiet",
            ])

        assert result.exit_code == 0, result.output
        # Quiet mode produces no output
        assert result.output.strip() == ""

    def test_default_project_id_from_config(self):
        client = _mock_client()
        with _patch_client(client), _patch_project_id("default-proj"):
            result = runner.invoke(app, ["graduate", "mem-uuid-111"])

        assert result.exit_code == 0, result.output
        client.graduate.assert_awaited_once_with(
            "mem-uuid-111",
            evidence=None,
            reviewer_note=None,
            project_id="default-proj",
        )

    def test_explicit_project_id_overrides_default(self):
        client = _mock_client()
        with _patch_client(client), _patch_project_id("default-proj"):
            result = runner.invoke(app, [
                "graduate", "mem-uuid-222", "--project-id", "override-proj",
            ])

        assert result.exit_code == 0, result.output
        client.graduate.assert_awaited_once_with(
            "mem-uuid-222",
            evidence=None,
            reviewer_note=None,
            project_id="override-proj",
        )
