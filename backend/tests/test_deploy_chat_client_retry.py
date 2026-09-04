"""DeployChatClient retries transient network errors."""
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.services.ai_chat_client import DeployChatClient


def test_complete_retries_connection_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com")
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    monkeypatch.setenv("LLM_RETRY_BACKOFF", "0")
    client = DeployChatClient()
    ok_resp = MagicMock()
    ok_resp.raise_for_status.return_value = None
    ok_resp.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    with patch("app.services.ai_chat_client.requests.post", side_effect=[
        requests.exceptions.ConnectionError("Remote end closed connection without response"),
        ok_resp,
    ]) as post:
        content, data = client.complete([{"role": "user", "content": "hi"}])
    assert post.call_count == 2
    assert content == '{"ok": true}'
    assert data["choices"][0]["message"]["content"] == '{"ok": true}'


def test_complete_does_not_retry_auth_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com")
    monkeypatch.setenv("LLM_MAX_RETRIES", "2")
    client = DeployChatClient()
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "unauthorized"
    http_err = requests.exceptions.HTTPError(response=resp)
    with patch("app.services.ai_chat_client.requests.post", return_value=resp) as post:
        resp.raise_for_status.side_effect = http_err
        with pytest.raises(ValueError, match="authentication failed"):
            client.complete([{"role": "user", "content": "hi"}])
    assert post.call_count == 1
