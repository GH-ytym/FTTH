"""Tests must never inherit a real model credential from the launch terminal."""
import os
import pytest


def authorize_message(service, sid, mid, action='plan'):
    """Service/tool unit fixture; end-to-end tests use real intent dispatch."""
    content = '执行本次规划' if action == 'plan' else '导出本次结果'
    wid = 'workflow-' + mid
    service.store.put('message', {'id': mid, 'session_id': sid, 'project_id': 'p',
        'content': content, 'workflow_id': wid})
    service.store.put('workflow', {'id': wid, 'session_id': sid, 'message_id': mid,
        'project_id': 'p', 'status': 'validating',
        'intent': {'action': action, 'evidence_quote': content},
        'authorization': {'source': 'user_request', 'action': action,
            'evidence_quote': content, 'request_content': content}})


@pytest.fixture(autouse=True)
def isolate_model_environment(monkeypatch):
    for key in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "FTTH_API_KEY", "FTTH_PROVIDER",
                "FTTH_MODEL", "FTTH_BASE_URL", "FTTH_MODEL_API", "FTTH_ENABLE_THINKING"):
        monkeypatch.delenv(key, raising=False)
    for key in list(os.environ):
        if key.startswith("FTTH_REVIEW_"):
            monkeypatch.delenv(key, raising=False)
