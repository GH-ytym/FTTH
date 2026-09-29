"""Browser capability boundary. No caller-supplied URLs or method names."""

import re
from urllib.parse import urlencode

ROUTES = {
    "health": ("GET", "/health"),
    "sessions": ("GET", "/sessions"),
    "createSession": ("POST", "/sessions"),
    "history": ("GET", "/sessions/{sid}/history-page"),
    "data": ("GET", "/sessions/{sid}/data"),
    "saveData": ("POST", "/sessions/{sid}/data"),
    "policy": ("POST", "/sessions/{sid}/policy"),
    "message": ("POST", "/sessions/{sid}/messages"),
    "cancelTurn": ("POST", "/sessions/{sid}/messages/{mid}/cancel"),
    "runs": ("GET", "/sessions/{sid}/runs"),
    "run": ("GET", "/sessions/{sid}/runs/{rid}"),
    "reviews": ("GET", "/sessions/{sid}/runs/{rid}/reviews"),
    "review": ("POST", "/sessions/{sid}/runs/{rid}/reviews"),
    "cancelRun": ("POST", "/sessions/{sid}/runs/{rid}/cancel"),
    "plan": ("POST", "/sessions/{sid}/runs"),
    "validate": ("POST", "/sessions/{sid}/validate"),
    "display": ("POST", "/sessions/{sid}/display"),
    "export": ("POST", "/sessions/{sid}/exports"),
    "compare": ("GET", "/sessions/{sid}/compare"),
    "resolve": ("POST", "/sessions/{sid}/workflows/{wid}/resolve"),
    "cancelWorkflow": ("POST", "/sessions/{sid}/workflows/{wid}/cancel"),
    "check": ("GET", "/sessions/{sid}/checks/{cid}"),
    "checkData": ("POST", "/sessions/{sid}/checks"),
}


def route(action, args):
    if action not in ROUTES:
        raise ValueError("未知操作")
    method, template = ROUTES[action]
    for name in re.findall(r"{(\w+)}", template):
        value = args.get(name)
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,100}", value
        ):
            raise ValueError("无效的对象编号")
    path = template.format(**args)
    if action == "history" and args.get("before"):
        value = args["before"]
        if type(value) is not int or value < 0:
            raise ValueError("历史游标无效")
        path += "?" + urlencode({"before": value})
    if action == "compare":
        if any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(args.get(k, "")))
            for k in ("left", "right")
        ):
            raise ValueError("无效的运行编号")
        path += "?" + urlencode({k: args[k] for k in ("left", "right")})
    return method, path
