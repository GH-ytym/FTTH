"""Asynchronous OpenAI-compatible chat-completions client using Qt Network."""

from __future__ import annotations

import json
import uuid

from qgis.PyQt.QtCore import QByteArray, QObject, QUrl, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


def chat_completions_url(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("模型接口地址不能为空")
    if value.endswith("/chat/completions"):
        return value
    return value + "/chat/completions"


def _known_header(name: str):
    try:
        return getattr(QNetworkRequest.KnownHeaders, name)
    except AttributeError:
        return getattr(QNetworkRequest, name)


def _network_error(reply: QNetworkReply):
    try:
        return reply.error() != QNetworkReply.NetworkError.NoError
    except AttributeError:
        return reply.error() != QNetworkReply.NoError


class ModelClient(QObject):
    completed = pyqtSignal(str, str, object)
    failed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self._replies = {}

    def chat(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict],
        temperature: float = 0.1,
        timeout_ms: int = 120000,
    ) -> str:
        model_name = str(model or "").strip()
        if not model_name:
            raise ValueError("模型 ID 不能为空")
        request_id = uuid.uuid4().hex
        request = QNetworkRequest(QUrl(chat_completions_url(base_url)))
        request.setHeader(_known_header("ContentTypeHeader"), "application/json")
        if str(api_key or "").strip():
            request.setRawHeader(
                QByteArray(b"Authorization"),
                QByteArray(("Bearer " + str(api_key).strip()).encode("utf-8")),
            )
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": float(temperature),
        }
        reply = self.manager.post(
            request,
            QByteArray(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        )
        if hasattr(reply, "setTransferTimeout"):
            reply.setTransferTimeout(int(timeout_ms))
        self._replies[request_id] = reply
        reply.finished.connect(lambda rid=request_id: self._finished(rid))
        return request_id

    def _finished(self, request_id: str):
        reply = self._replies.pop(request_id, None)
        if reply is None:
            return
        try:
            body = bytes(reply.readAll()).decode("utf-8", errors="replace")
            if _network_error(reply):
                self.failed.emit(request_id, f"网络错误：{reply.errorString()}\n{body[:1000]}")
                return
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self.failed.emit(request_id, "模型服务没有返回 JSON：" + body[:1000])
                return
            if isinstance(data, dict) and data.get("error"):
                self.failed.emit(
                    request_id,
                    "模型服务错误：" + json.dumps(data["error"], ensure_ascii=False),
                )
                return
            choices = data.get("choices", []) if isinstance(data, dict) else []
            if not choices:
                self.failed.emit(request_id, "模型回复缺少 choices")
                return
            message = choices[0].get("message", {}) or {}
            content = message.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    str(item.get("text", "")) if isinstance(item, dict) else str(item)
                    for item in content
                )
            self.completed.emit(request_id, str(content), data)
        finally:
            reply.deleteLater()

    def cancel_all(self):
        for reply in list(self._replies.values()):
            reply.abort()
            reply.deleteLater()
        self._replies.clear()
