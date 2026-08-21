"""Tests for Model save instrumentation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.django_db
@patch("nova.typing.models.nova_span")
def test_model_save_tracing_lifecycle(mock_nova_span):
    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=False)

    def span_side_effect(name, **kwargs):
        mock_span.reset_mock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)
        return mock_span

    mock_nova_span.side_effect = span_side_effect

    from tests.models import Article

    article = Article(title="Traced", body="Content")
    article.save()

    calls = mock_nova_span.call_args_list

    assert calls[0][0][0] == "nova.model.save"
    assert calls[0][1]["model"] == "tests.Article"
    assert "database" in calls[0][1]
    assert "table" in calls[0][1]

    assert calls[1][0][0] == "nova.validation.run"

    mock_span.set_attribute.assert_any_call("validation.time_ms", pytest.approx(0, abs=100))
    mock_span.set_attribute.assert_any_call("validation.passed", True)
