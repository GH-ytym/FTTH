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
    for key in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "FTTH_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    # No setting, config file or installed QGIS on the developer's machine
    # may change test outcomes; runtime tests build explicit fake layouts.
    for key in list(os.environ):
        if key.startswith("FTTH_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("backend.qgis_runtime.candidates", lambda: [])


@pytest.fixture(autouse=True)
def release_log_handlers():
    """`ftth` commands attach file handlers globally; close them after each test."""
    yield
    import logging
    from backend.logs import HANDLER_NAME
    for name in ("ftth", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        for handler in [h for h in logger.handlers if h.get_name() == HANDLER_NAME]:
            logger.removeHandler(handler)
            handler.close()


class MemoryKeyring:
    """In-process stand-in for the OS credential store."""
    priority = 1

    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError
        if (service, username) not in self.values:
            raise PasswordDeleteError(username)
        del self.values[(service, username)]


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch):
    """Never read or write the developer's real Keychain / Credential Manager."""
    import keyring
    from keyring.backend import KeyringBackend
    store = type("TestKeyring", (MemoryKeyring, KeyringBackend), {})()
    monkeypatch.setattr(keyring.core, "_keyring_backend", store)
    # Child processes started by tests get a store that holds nothing.
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    yield store
