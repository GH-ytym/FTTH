"""Actual QtWebEngine DOM and bridge acceptance; no browser mocks."""

import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get("DEMO_TEST_PLUGINS", str(ROOT / "qgis_plugin")))
from qgis.PyQt.QtCore import Qt, QSettings
from qgis.core import QgsApplication

QgsApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
app = QgsApplication([], True)
app.initQgis()
from ftth_demo0.web_panel import WebPanel

settings = QSettings(os.environ["FTTH_PLUGIN_SETTINGS"], QSettings.IniFormat)
settings.setValue("url", os.environ["DEMO_TEST_URL"])
settings.setValue("token_path", os.environ["DEMO_TEST_TOKEN_FILE"])
settings.sync()
p = WebPanel(None)
p.resize(520, 900)
p.setAttribute(Qt.WA_DontShowOnScreen)
p.show()
errors = []
p.page.javaScriptConsoleMessage = lambda level, message, line, source: (
    errors.append(message) if level == 2 else None
)


def wait(fn, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if fn():
            return
        time.sleep(0.02)
    raise AssertionError("Web UI timeout: " + str(errors))


def js(code):
    result = []
    p.page.runJavaScript(code, result.append)
    wait(lambda: bool(result))
    return result[0]


def text():
    return js("document.body.innerText") or ""


def click(title):
    assert js(
        "(()=>{let x=document.querySelector('[aria-label=\""
        + title
        + "\"]');if(!x)return false;x.click();return true})()"
    ), title


wait(lambda: "未配置模型" in text() or "fixture" in text())
# Offline production bundle renders at screenshot reference width.
out = ROOT / "runtime/release-0.8.0"
out.mkdir(parents=True, exist_ok=True)
app.processEvents()
p.grab().save(str(out / "web-home.png"))
click("项目数据")
wait(lambda: "导入道路 GPKG" in text())
assert "字段映射与校验" not in text() and "保存工程策略" not in text()
js("document.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));document.querySelector('section input').focus()")
assert js("getComputedStyle(document.activeElement).outlineStyle") == "none"
assert js("getComputedStyle(document.activeElement).boxShadow") == "none"
p.grab().save(str(out / "web-pointer-focus.png"))
js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true}));document.querySelector('section button').focus()")
# Hidden automation windows do not acquire native focus. Verify modality and
# the keyboard rule separately; mouse/Tab appearance still needs manual QA.
assert js("document.documentElement.dataset.navigation") == "keyboard"
assert js("Array.from(document.styleSheets).some(s=>Array.from(s.cssRules).some(r=>(r.selectorText||'').includes('data-navigation')&&r.style.boxShadow.includes('inset')))")
js("document.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))")
assert js("getComputedStyle(document.activeElement).boxShadow") == "none"
p.grab().save(str(out / "web-data.png"))
click("历史会话")
wait(lambda: bool(js("!!document.querySelector('.history input')")))
js("document.querySelector('.history input').focus()")
assert js("getComputedStyle(document.activeElement).outlineStyle") == "none"
assert js("getComputedStyle(document.activeElement).boxShadow") == "none"
p.grab().save(str(out / "web-search-focus.png"))
click("关闭")
click("设置")
wait(lambda: "连接诊断" in text())
assert os.environ["DEMO_TEST_SECRET"] not in text()
click("返回")
wait(
    lambda: (
        "描述你的规划任务"
        in (js("document.querySelector('textarea')?.placeholder") or "")
    )
)
js(
    "(()=>{let e=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(e,'你好，请说明如何使用');e.dispatchEvent(new Event('input',{bubbles:true}));})()"
)
wait(lambda: not js("document.querySelector('button[title=发送]').disabled"))
js("document.querySelector('button[title=发送]').click()")
wait(lambda: "模型未配置" in text())
assert "你好，请说明如何使用" in text()
p.grab().save(str(out / "web-chat.png"))
# A renderer reload restores the current conversation from the backend snapshot.
loaded = []
p.view.loadFinished.connect(loaded.append)
p.view.reload()
wait(lambda: bool(loaded))
wait(lambda: "你好，请说明如何使用" in text())
assert "模型未配置" in text()
# Native bridge capability rejection is checked directly, with real signals.
responses = []
p.bridge.response.connect(lambda raw: responses.append(json.loads(raw)))
p.bridge.request(
    json.dumps({"id": "bad", "action": "shell", "args": {"command": "whoami"}})
)
wait(lambda: any(r["id"] == "bad" for r in responses))
assert next(r for r in responses if r["id"] == "bad")["error"]
# Malicious Markdown HTML is never interpreted by React.
js("window.__xss=0")
p.bridge.event.emit(
    json.dumps(
        {
            "type": "events",
            "sid": p.sid,
            "items": [
                {
                    "id": 999999,
                    "type": "assistant",
                    "message_id": "security",
                    "status": "succeeded",
                    "text": "<img src=x onerror=window.__xss=1> [bad](javascript:alert(1))",
                }
            ],
        }
    )
)
app.processEvents()
assert js("window.__xss") == 0
assert not js("!!document.querySelector('a[href^=\"javascript:\"]')")
p.bridge.event.emit(
    json.dumps(
        {
            "type": "events",
            "sid": p.sid,
            "items": [
                {
                    "id": 1000000,
                    "type": "assistant",
                    "message_id": "rich-fixture",
                    "status": "succeeded",
                    "text": '**界面夹具：规划已完成**\n\n这条内容仅用于排版验收。独立审查有 1 项警告，请核实管孔容量。\n\n|阶段|状态|\n|---|---|\n|计算|完成|\n|审查|需要复核|\n\n```json\n{"status":"completed"}\n```',
                }
            ],
        }
    )
)
wait(lambda: bool(js('!!document.querySelector("table")')))
assert js('document.querySelectorAll(".code button").length') > 0
p.grab().save(str(out / "web-rich.png"))
p.resize(360, 850)
app.processEvents()
assert js("document.documentElement.scrollWidth<=window.innerWidth")
p.grab().save(str(out / "web-narrow.png"))
assert not errors, errors
p.shutdown()
p.close()
print(
    "PASS: WebEngine production UI, navigation, real HTTP message, capability rejection, Markdown safety, 360px, no token in DOM"
)
