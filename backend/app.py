from contextlib import asynccontextmanager
import hmac
import json

from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .agent import AgentController
from .models import AckInput, DisplayInput, ExportInput, MessageInput, ProjectInput, RunInput, SessionInput, PolicyInput, ReviewInput
from .review import ReviewController
from .service import Service, run_summary
from .settings import Settings
from .store import Store
from .titles import fallback_title
from .models import WorkflowChoice


def create_app(settings=None, model=None, review_model=None):
    settings = settings or Settings()
    store = Store(settings.data / "app.sqlite")

    @asynccontextmanager
    async def lifespan(app):
        service = None
        try:
            service = Service(settings, store)
            async with AsyncSqliteSaver.from_conn_string(str(settings.data / "checkpoints.sqlite")) as saver:
                agent = AgentController(service, saver, model)
                service.on_complete = agent.completed
                service.reviewer = ReviewController(service, review_model)
                service.on_review = agent.review_completed
                app.state.service, app.state.agent = service, agent
                try:
                    yield
                finally:
                    await service.close()
        finally:
            if service:
                await service.close()
            store.db.close()

    async def authenticate(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + settings.token):
            raise HTTPException(401, "需要本地连接令牌")

    app = FastAPI(title="FTTH demo0", lifespan=lifespan, dependencies=[Depends(authenticate)])

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.6.0", "execution_authority": "user_request", "model_configured": settings.model_ready, "model": settings.model,
                "model_api": settings.model_api, "provider": settings.provider,
                "qgis_python_exists": (settings.qgis / "bin/python.exe").exists()}

    @app.post("/projects")
    async def project(spec: ProjectInput):
        return await app.state.service.register(spec)

    @app.post("/sessions")
    def session(spec: SessionInput):
        return app.state.service.session(spec.project_id)

    @app.get("/sessions")
    def sessions():
        items = store.all("session")
        messages = store.all("message")
        for item in items:
            first = next((m for m in messages if m["session_id"] == item["id"] and not m.get("notification")), None)
            item.setdefault("title", fallback_title(first["content"]) if first else "新对话")
        return sorted(items, key=lambda s: s.get("updated", s.get("created", 0)), reverse=True)

    @app.post("/sessions/{sid}/project")
    def bind_project(sid: str, spec: SessionInput):
        if not spec.project_id:
            raise ValueError("请选择项目数据")
        return app.state.service.bind_project(sid, spec.project_id)

    @app.get("/sessions/{sid}/history")
    def history(sid: str):
        with store.lock:
            session = store.get("session", sid)
            messages = [{k: m[k] for k in ("id", "content", "response", "partial_response", "stream_id", "status", "notification", "elapsed_seconds", "workflow_id", "action_status") if k in m}
                for m in sorted(store.all("message"), key=lambda m: m.get("created", 0)) if m["session_id"] == sid]
            for message in messages:
                if message.get("notification"):
                    message.pop("content", None)
            cursor = store.db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE session=?", (sid,)).fetchone()[0]
            activity = [dict(id=row[0], **json.loads(row[1])) for row in store.db.execute("SELECT id,data FROM events WHERE session=? AND id<=? ORDER BY id", (sid, cursor))]
            return {"session": session, "messages": messages, "cursor": cursor,
                "timeline": [e for e in activity if e['type'] not in {'assistant_stream', 'progress'}],
                "workflows": [w for w in store.all('workflow') if w['session_id'] == sid],
                "activities": [e for e in activity if e["type"] in ("activity", "progress", "run", "algorithm", "review")]}

    @app.get('/sessions/{sid}/workflows/{wid}')
    def workflow(sid: str, wid: str):
        return app.state.agent.workflows.owned(sid, wid)

    @app.post('/sessions/{sid}/workflows/{wid}/resolve')
    async def resolve_workflow(sid: str, wid: str, choice: WorkflowChoice):
        return app.state.agent.workflows.resolve(sid, wid, choice)

    @app.post('/sessions/{sid}/workflows/{wid}/cancel')
    async def cancel_workflow(sid: str, wid: str):
        return await app.state.agent.workflows.cancel(sid, wid)

    @app.post("/sessions/{sid}/policy")
    def policy(sid: str, spec: PolicyInput):
        return app.state.service.set_policy(sid, spec)

    @app.post("/sessions/{sid}/messages", status_code=202)
    async def message(sid: str, spec: MessageInput):
        return app.state.agent.enqueue(sid, spec)

    @app.get("/sessions/{sid}/events")
    def events(sid: str, after: int = 0):
        store.get("session", sid)
        return store.events(sid, after)

    @app.get("/sessions/{sid}/project")
    def inspect(sid: str):
        return app.state.service.inspect(sid)

    @app.post("/sessions/{sid}/validate")
    def validate(sid: str, spec: RunInput):
        return app.state.service.validate(sid, spec)

    @app.post("/sessions/{sid}/runs", status_code=202)
    async def run(sid: str, spec: RunInput):
        service = app.state.service
        result = service.submit(sid, spec)
        return result

    @app.get("/sessions/{sid}/runs/{rid}/reviews")
    def reviews(sid: str, rid: str):
        return app.state.service.reviewer.list(sid, rid)

    @app.post("/sessions/{sid}/runs/{rid}/reviews", status_code=202)
    async def review(sid: str, rid: str, spec: ReviewInput):
        return app.state.service.reviewer.enqueue(sid, rid, "manual:" + spec.request_id)

    @app.get("/sessions/{sid}/runs")
    def runs(sid: str):
        store.get("session", sid)
        return [run_summary(r) for r in store.all("run") if r["session_id"] == sid]

    @app.get("/sessions/{sid}/runs/{rid}")
    def get_run(sid: str, rid: str):
        return app.state.service.owned_run(sid, rid)

    @app.post("/sessions/{sid}/runs/{rid}/cancel")
    async def cancel(sid: str, rid: str):
        return await app.state.service.cancel(sid, rid)

    @app.get("/sessions/{sid}/compare")
    def compare(sid: str, left: str, right: str):
        return app.state.service.compare(sid, left, right)

    @app.post("/sessions/{sid}/exports", status_code=202)
    async def export(sid: str, spec: ExportInput):
        return app.state.service.export(sid, spec)

    @app.post("/sessions/{sid}/display", status_code=202)
    def display(sid: str, spec: DisplayInput):
        return app.state.service.show(sid, spec.run_id, spec.visible_layers, spec.zoom)

    @app.get("/sessions/{sid}/display")
    def pending(sid: str):
        store.get("session", sid)
        return [c for c in store.all("display") if c["session_id"] == sid and c["status"] == "pending"]

    @app.post("/sessions/{sid}/display/{cid}/ack")
    def ack(sid: str, cid: str, spec: AckInput):
        return app.state.service.ack(sid, cid, spec)

    return app
