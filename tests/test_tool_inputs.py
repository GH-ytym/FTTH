import asyncio
import json

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from backend.agent import AgentController
from backend.models import RunInput
from backend.models import AckInput, PolicyInput
from test_service import setup_service


def test_model_tools_decode_object_and_preserve_inheritance(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    service.spawn = lambda coro: coro.close()
    tools = {t.name: t for t in AgentController(service, None).tools(sid, "msg")}
    changes = {"use_formal_infrastructure": True, "use_recognition_roads": False, "split_oversized_imbs": True}
    service.set_policy(sid, PolicyInput(**changes))
    service.store.put("message", {"id": "msg", "session_id": sid, "allow_actions": True, "project_id": "p"})
    args = {"scope": "full", "parameter_overrides": changes}
    assert tools["validate_inputs"].invoke(args)["valid"]
    encoded = {**args, "parameter_overrides": json.dumps(changes)}
    assert tools["validate_inputs"].invoke(encoded)["valid"]
    first = asyncio.run(tools["submit_planning"].ainvoke(encoded))
    assert first["parameters"]["use_formal_infrastructure"] is True
    assert asyncio.run(tools["submit_planning"].ainvoke(args))["id"] == first["id"]
    second = asyncio.run(tools["submit_planning"].ainvoke({"base_run_id": first["id"],
        "parameter_overrides": '{"zpm_max_cap":600}'}))
    assert second["parameters"]["zpm_max_cap"] == 600
    assert second["parameters"]["use_formal_infrastructure"] is True
    assert second["scope"] == "full"
    assert tools["validate_inputs"].invoke({"base_run_id": second["id"]})["valid"]


@pytest.mark.parametrize("value", [
    '{"use_formal_infrastructure":"true"}', '{"zpm_max_cap":0}',
    '{"zpm_max_cap":null}', '{"unknown":1}', '[]', 'null', 'invalid',
    '"{\\"use_formal_infrastructure\\":true}"',
])
def test_tool_serialization_fix_does_not_relax_validation(tmp_path, value):
    service, sid, _ = setup_service(tmp_path)
    tools = AgentController(service, None).tools(sid, "msg")
    for tool in tools[1:3]:
        with pytest.raises(ValueError):
            asyncio.run(tool.ainvoke({"parameter_overrides": value}))
    assert service.store.all("run") == []
    with pytest.raises(ValueError):
        RunInput(request_id="http", parameter_overrides=value)


def test_provider_receives_named_fields_and_constraints(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    tool = AgentController(service, None).tools(sid, "msg")[1]
    schema = convert_to_openai_tool(tool)["function"]["parameters"]
    overrides = schema["properties"]["parameter_overrides"]
    assert overrides["type"] == "object"
    assert overrides["additionalProperties"] is False
    assert overrides["properties"]["use_formal_infrastructure"]["type"] == "boolean"
    assert overrides["properties"]["zpm_max_cap"]["minimum"] == 1


def test_display_list_and_completion_reuses_receipt(tmp_path):
    service, sid, _ = setup_service(tmp_path)
    service.store.put("run", {"id": "r", "session_id": sid, "kind": "planning",
        "status": "succeeded", "result": {}})
    controller = AgentController(service, None)
    show = controller.tools(sid, "user")[-2]
    result = show.invoke({"run_id": "r", "visible_layers": '["IMB","CABLE"]'})
    command = service.store.get("display", result["command_id"])
    assert command["visible_layers"] == ["IMB", "CABLE"]
    for invalid in ('{}', '["UNKNOWN"]', '[1]', 'null'):
        with pytest.raises(ValueError):
            show.invoke({"run_id": "r", "visible_layers": invalid})
    complete_show = controller.tools(sid, "completion", notification=True)[-2]
    assert complete_show.invoke({"run_id": "r"})["status"] == "pending"
    service.ack(sid, command["id"], AckInput(success=True, details={"layers": {"IMB": 51}}))
    assert complete_show.invoke({"run_id": "r"})["status"] == "succeeded"
    assert len(service.store.all("display")) == 1
    # A later explicit user request must still be able to reload the map.
    show.invoke({"run_id": "r"})
    assert len(service.store.all("display")) == 2
