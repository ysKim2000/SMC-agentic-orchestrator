from pydantic import BaseModel, Field, model_validator
from typing import List, Dict, Optional
from typing import Literal


# ── Admin CRUD 스키마 ─────────────────────────────────

class DepartmentCreate(BaseModel):
    department_name: str

class DepartmentUpdate(BaseModel):
    new_name: str

class ProjectCreate(BaseModel):
    project_name: str

class ProjectUpdate(BaseModel):
    new_name: str

class ModelCreate(BaseModel):
    model_name: str
    model_description: str
    model_path: Dict[str, str]
    required_data: List[str]
    task_type: str
    result_type: str
    inference_script: Optional[str] = None

class ModelUpdate(BaseModel):
    new_name: str
    department_name: str
    project_name: str

class ModelDelete(BaseModel):
    department_name: str
    project_name: str


class RiskPolicyRule(BaseModel):
    tier: Literal["Low", "High", "Critical"]
    labels: List[str] = Field(default_factory=list)
    prediction_value: int = 1
    min_probability: float | None = Field(default=None, ge=0, le=1)
    detection_count_gte: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_condition(self):
        if not self.labels and self.detection_count_gte is None:
            raise ValueError("labels or detection_count_gte is required")
        return self


class RiskPolicy(BaseModel):
    rules: List[RiskPolicyRule] = Field(default_factory=list)
    default_tier: Literal["Low", "High", "Critical"] | None = None
