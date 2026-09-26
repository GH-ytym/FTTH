from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Parameters(StrictModel):
    nro_max_cap: int = Field(2048, ge=1, le=1000000)
    zpm_max_cap: int = Field(576, ge=1, le=1000000)
    spatial_weight: float = Field(1.0, ge=0, le=10000)
    pipe_weight: float = Field(100.0, ge=0, le=10000)
    formal_duct_capacity: int = Field(2048, ge=1, le=1000000)
    pbo_offset_m: float = Field(5.0, gt=0, le=1000)
    candidate_paths: int = Field(5, ge=1, le=20)
    water_cost_factor: float = Field(2.0, ge=0, le=1000)
    ptech_max_spacing_m: float = Field(200.0, gt=0, le=100000)
    terminal_snap_distance_m: float = Field(80.0, gt=0, le=100000)
    ga_population: int = Field(14, ge=4, le=100)
    ga_generations: int = Field(10, ge=1, le=100)
    ga_refine: bool = True
    split_oversized_imbs: bool = False
    use_formal_infrastructure: bool = False
    use_recognition_roads: bool = False


class ProjectInput(StrictModel):
    name: str = "当前项目"
    config_path: str
    recognition_path: str | None = None


class SessionInput(StrictModel):
    project_id: str | None = None


class RunInput(StrictModel):
    request_id: str = Field(min_length=1)
    allow_auto_repair: bool = False
    base_run_id: str | None = None
    scope: Literal["partition", "full"] | None = None
    parameter_overrides: dict = Field(default_factory=dict)


class MessageInput(StrictModel):
    request_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=20000)
    # Accepted for older clients only; execution authority comes from the
    # current request's interpreted workflow, never from this client flag.
    allow_actions: bool | None = Field(default=None, deprecated=True)
    allow_auto_repair: bool = False


class ReviewInput(StrictModel):
    request_id: str = Field(min_length=1, max_length=200)


class WorkflowChoice(StrictModel):
    request_id: str = Field(min_length=1, max_length=200)
    option_id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)


class PolicyInput(StrictModel):
    use_formal_infrastructure: bool = False
    use_recognition_roads: bool = False
    split_oversized_imbs: bool = False


class DisplayInput(StrictModel):
    run_id: str
    visible_layers: list[str] = Field(default_factory=list)
    zoom: bool = True


class AckInput(StrictModel):
    success: bool
    details: dict = Field(default_factory=dict)


class ExportInput(StrictModel):
    request_id: str
    run_id: str
