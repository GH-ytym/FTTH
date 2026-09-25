"""Typed, partial planning arguments for model tools; HTTP input stays strict."""
import json
from copy import deepcopy
from typing import Literal

from pydantic import Field, create_model, field_validator

from .models import Parameters, StrictModel


def partial_parameters():
    fields = {}
    for name, original in Parameters.model_fields.items():
        field = deepcopy(original)
        # An omitted field must not overwrite the inherited configuration.
        field.default = None
        fields[name] = (original.annotation, field)
    return create_model("ParameterOverrides", __base__=StrictModel, **fields)


ParameterOverrides = partial_parameters()


class PlanningToolInput(StrictModel):
    scope: Literal["full", "partition"] | None = None
    parameter_overrides: ParameterOverrides = Field(default_factory=ParameterOverrides,
        description="Only explicitly changed fields, as a JSON object, never a quoted JSON string.")
    base_run_id: str | None = None

    @field_validator("parameter_overrides", mode="before")
    @classmethod
    def decode_object(cls, value):
        # Some compatible providers double-encode nested objects. Decode once,
        # then apply exactly the same field types, bounds and unknown-key checks.
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ValueError("parameter_overrides 必须为 JSON 对象") from exc
            if not isinstance(value, dict):
                raise ValueError("parameter_overrides 必须为 JSON 对象")
        return value


def overrides_dict(value):
    return value.model_dump(exclude_unset=True) if value is not None else {}


class ShowToolInput(StrictModel):
    run_id: str
    visible_layers: list[Literal["IMB", "SITE", "PTECH", "BOITE", "INFRASTRUCTURE", "CABLE", "ZPM", "ZNRO"]] | None = None
    zoom: bool = True

    @field_validator("visible_layers", mode="before")
    @classmethod
    def decode_list(cls, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ValueError("visible_layers 必须为 JSON 数组") from exc
            if not isinstance(value, list):
                raise ValueError("visible_layers 必须为 JSON 数组")
        return value
