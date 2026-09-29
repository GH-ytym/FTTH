"""Offline React host. Credentials and QGIS capabilities stay in Python."""

import json
import os
import uuid
from pathlib import Path
from qgis.PyQt.QtCore import (
    QObject,
    QUrl,
    QTimer,
    QSettings,
    QByteArray,
    pyqtSlot,
    pyqtSignal,
)
from qgis.PyQt.QtWidgets import QWidget, QVBoxLayout, QFileDialog, QApplication
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWebEngineWidgets import (
    QWebEngineView,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineCore import QWebEngineUrlRequestInterceptor
from .connection import resolve, read_token
from .web_contract import route
from .display import show_result

ASSETS = Path(__file__).parent / "web"


def local_url(url):
    if url.scheme() == "qrc":
        return url.path() == "/qtwebchannel/qwebchannel.js"
    if not url.isLocalFile():
        return False
    try:
        return Path(url.toLocalFile()).resolve().is_relative_to(ASSETS.resolve())
    except (ValueError, OSError):
        return False


class Guard(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if not local_url(info.requestUrl()):
            info.block(True)


class Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main):
        return (
            main
            and url.isLocalFile()
            and Path(url.toLocalFile()).resolve() == (ASSETS / "index.html").resolve()
        )

    def createWindow(self, kind):
        return None


class Bridge(QObject):
    response = pyqtSignal(str)
    event = pyqtSignal(str)

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    @pyqtSlot(str)
    def request(self, raw):
        request = {}
        try:
            if len(raw) > 1000000:
                raise ValueError("请求过大")
            request = json.loads(raw)
            if not isinstance(request, dict) or not isinstance(request.get("id"), str):
                raise ValueError("无效请求")
            self.panel.dispatch(request)
        except Exception as exc:
            self.panel.answer(request.get("id", "") if isinstance(request, dict) else "", error=str(exc))


class WebPanel(QWidget):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.closed = False
        isolated = os.environ.get("FTTH_PLUGIN_SETTINGS")
        self.settings = (
            QSettings(isolated, QSettings.IniFormat)
            if isolated
            else QSettings("FTTH", "demo0")
        )
        self.url, self.token_path = resolve(
            self.settings.value("url"), self.settings.value("token_path")
        )
        self.token = read_token(self.token_path)
        self.net = QNetworkAccessManager(self)
        self.pending = set()
        self.polling = False
        self.sid = ""
        self.cursor = 0
        self.generation = 0
        self.receipts = {}
        self.view = QWebEngineView(self)
        self.profile = QWebEngineProfile(self.view)
        self.guard = Guard(self.profile)
        self.profile.setUrlRequestInterceptor(self.guard)
        self.profile.downloadRequested.connect(lambda item: item.cancel())
        self.page = Page(self.profile, self.view)
        self.view.setPage(self.page)
        self.page.settings().setAttribute(
            QWebEngineSettings.LocalContentCanAccessRemoteUrls, False
        )
        self.page.settings().setAttribute(
            QWebEngineSettings.JavascriptCanOpenWindows, False
        )
        self.channel = QWebChannel(self.page)
        self.bridge = Bridge(self)
        self.channel.registerObject("host", self.bridge)
        self.page.setWebChannel(self.channel)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.view)
        self.view.load(QUrl.fromLocalFile(str((ASSETS / "index.html").resolve())))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(500)
        self.page.renderProcessTerminated.connect(self.crashed)

    def answer(self, rid, value=None, error=None):
        if self.closed:
            return
        if error:
            error = error.replace(self.token, "[redacted]") if self.token else error
        self.bridge.response.emit(
            json.dumps({"id": rid, "value": value, "error": error}, ensure_ascii=False)
        )

    def emit(self, kind, **values):
        if not self.closed:
            self.bridge.event.emit(
                json.dumps({"type": kind, **values}, ensure_ascii=False)
            )

    def http(self, method, path, payload, done, failed):
        req = QNetworkRequest(QUrl(self.url.rstrip("/") + path))
        req.setRawHeader(b"Authorization", ("Bearer " + self.token).encode())
        req.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.ManualRedirectPolicy,
        )
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        req.setTransferTimeout(120000)
        reply = (
            self.net.get(req)
            if method == "GET"
            else self.net.post(
                req, QByteArray(json.dumps(payload or {}, ensure_ascii=False).encode())
            )
        )
        self.pending.add(reply)

        def finish():
            self.pending.discard(reply)
            try:
                status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
                data = json.loads(bytes(reply.readAll()).decode() or "{}")
                if reply.error() != QNetworkReply.NoError or not 200 <= status < 300:
                    raise ValueError(str(data.get("detail", "后端连接失败")))
                if not self.closed:
                    done(data)
            except Exception as exc:
                if not self.closed:
                    failed(str(exc))
            finally:
                reply.deleteLater()

        reply.finished.connect(finish)

    def dispatch(self, r):
        rid = r["id"]
        action = r.get("action")
        args = r.get("args") or {}
        if action == "bootstrap":
            return self.answer(
                rid,
                {
                    "url": self.url,
                    "tokenConfigured": bool(self.token),
                    "protocol": "web-v1",
                    "session_id": self.sid,
                },
            )
        if action == "subscribe":
            # Validate the session identifier before it reaches an internal route.
            route("history", args)
            self.sid = args["sid"]
            self.cursor = int(args.get("cursor", 0))
            self.generation += 1
            return self.answer(rid, True)
        if action == "unsubscribe":
            self.sid = ""
            self.generation += 1
            return self.answer(rid, True)
        if action == "copy":
            QApplication.clipboard().setText(str(args.get("text", ""))[:200000])
            return self.answer(rid, True)
        if action == "external":
            url = QUrl(str(args.get("url", "")))
            if url.scheme() not in {"http", "https"}:
                raise ValueError("链接协议不允许")
            QDesktopServices.openUrl(url)
            return self.answer(rid, True)
        if action == "connection":
            url = QUrl(str(args.get("url", "")))
            if (
                url.scheme() not in {"http", "https"}
                or not url.host()
                or url.userInfo()
                or url.hasQuery()
                or url.hasFragment()
            ):
                raise ValueError("连接地址无效")
            if (url.scheme(), url.host(), url.port()) != (
                QUrl(self.url).scheme(),
                QUrl(self.url).host(),
                QUrl(self.url).port(),
            ):
                self.token = ""
                self.token_path = ""
                self.settings.setValue("token_path", "")
                self.sid = ""
                self.generation += 1
            self.url = url.toString().rstrip("/")
            self.settings.setValue("url", self.url)
            return self.answer(rid, True)
        if action == "chooseToken":
            path, _ = QFileDialog.getOpenFileName(self, "选择本地 connection.token")
            if path:
                self.token_path = path
                self.token = read_token(path)
                self.settings.setValue("token_path", path)
            return self.answer(rid, bool(self.token))
        if action == "chooseRoads":
            path, _ = QFileDialog.getOpenFileName(self, "选择识别道路 GPKG", "", "GeoPackage (*.gpkg)")
            return self.answer(rid, path)
        if action == "chooseFile":
            path, _ = QFileDialog.getOpenFileName(
                self,
                "选择数据文件",
                "",
                "GIS / 表格 (*.gpkg *.shp *.geojson *.csv *.xlsx)",
            )
            return self.answer(rid, path)
        if action == "importConfig":
            path, _ = QFileDialog.getOpenFileName(
                self, "导入输入配置", "", "JSON (*.json)"
            )
            if not path:
                return self.answer(rid, None)
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            sources = raw.get("sources", {})
            if not sources and raw.get("vector_layers"):
                sources = {
                    v.get("biz_layer_name") or v.get("name"): {
                        "path": v.get("source"),
                        "source_layer": v.get("source_layer", ""),
                    }
                    for v in raw["vector_layers"]
                }
            if isinstance(sources, list):
                sources = {v.get("name", v.get("layer", "")): v for v in sources}
            clean = {}
            for name, v in sources.items():
                if (
                    name in {"IMB", "SITE", "PTECH", "INFRASTRUCTURE"}
                    and isinstance(v, dict)
                    and v.get("path")
                ):
                    source = Path(v["path"])
                    clean[name] = {
                        "path": str(
                            source
                            if source.is_absolute()
                            else (Path(path).parent / source).resolve()
                        ),
                        "source_layer": v.get("source_layer") or "",
                    }
            if not clean:
                raise ValueError("配置未包含可识别的数据源，请直接选择源文件")
            return self.answer(
                rid, {"sources": clean, "recognition_path": raw.get("recognition_path")}
            )
        if action == "currentLayer":
            from qgis.core import QgsVectorFileWriter, QgsProject

            name = args.get("name")
            if name not in {"IMB", "SITE", "PTECH", "INFRASTRUCTURE"}:
                raise ValueError("无效数据类别")
            layer = self.iface.activeLayer() if self.iface else None
            if layer is None or not hasattr(layer, "getFeatures"):
                raise ValueError("请在 QGIS 中选择矢量图层")
            folder = Path.home() / ".ftth_demo0" / "inputs" / uuid.uuid4().hex
            folder.mkdir(parents=True)
            path = folder / (name + ".gpkg")
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = name
            opts.onlySelectedFeatures = layer.selectedFeatureCount() > 0
            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, str(path), QgsProject.instance().transformContext(), opts
            )
            if result[0] != QgsVectorFileWriter.NoError:
                raise ValueError("图层快照失败")
            return self.answer(rid, {"path": str(path), "source_layer": name})
        if action == "expand":
            dock = self.parentWidget()
            if hasattr(dock, "setFloating"):
                dock.setFloating(True)
                dock.resize(900, 850)
            return self.answer(rid, True)
        method, path = route(action, args)
        self.http(
            method,
            path,
            args.get("payload"),
            lambda value: self.answer(rid, value),
            lambda error: self.answer(rid, error=error),
        )

    def poll(self):
        if self.closed or not self.sid or self.polling:
            return
        sid, gen = self.sid, self.generation
        self.polling = True

        def done(items):
            self.polling = False
            if sid != self.sid or gen != self.generation:
                return
            if items:
                self.cursor = max(self.cursor, max(x["id"] for x in items))
                self.emit("events", sid=sid, items=items)
            self.http(
                "GET",
                f"/sessions/{sid}/display",
                None,
                lambda commands: self.displays(sid, commands),
                lambda _: None,
            )

        def fail(error):
            self.polling = False
            self.emit("connectionError", sid=sid, message="连接中断，正在重试")

        self.http(
            "GET", f"/sessions/{sid}/events?after={self.cursor}", None, done, fail
        )

    def displays(self, sid, commands):
        if self.closed or sid != self.sid:
            return
        for c in commands:
            if c["id"] not in self.receipts:
                try:
                    self.receipts[c["id"]] = {
                        "success": True,
                        "details": show_result(self.iface, c),
                    }
                except Exception as exc:
                    self.receipts[c["id"]] = {
                        "success": False,
                        "details": {"error": str(exc)},
                    }
            self.http(
                "POST",
                f"/sessions/{sid}/display/{c['id']}/ack",
                self.receipts[c["id"]],
                lambda _: None,
                lambda _: None,
            )

    def crashed(self, *args):
        from qgis.PyQt.QtWidgets import QLabel, QPushButton

        self.view.hide()
        label = QLabel("网页界面已中断，后台任务仍保留。", self)
        self.layout().addWidget(label)
        button = QPushButton("重新加载界面", self)
        self.layout().addWidget(button)

        def reload():
            label.deleteLater()
            button.deleteLater()
            self.view.show()
            self.view.reload()

        button.clicked.connect(reload)

    def shutdown(self):
        self.closed = True
        self.timer.stop()
        for reply in list(self.pending):
            reply.abort()
        self.page.setWebChannel(None)
