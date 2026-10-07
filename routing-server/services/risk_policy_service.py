from typing import Any


RISK_ORDER = {"Low": 0, "High": 1, "Critical": 2}


def _rule_matches(rule: dict, result: dict) -> bool:
    labels = {str(value).lower() for value in rule.get("labels") or []}
    if labels:
        for prediction in result.get("predictions") or []:
            if not isinstance(prediction, dict):
                continue
            if str(prediction.get("label", "")).lower() not in labels:
                continue
            expected = rule.get("prediction_value", 1)
            if prediction.get("pred") != expected:
                continue
            minimum = rule.get("min_probability")
            probability = prediction.get("prob", prediction.get("score"))
            if minimum is None or (
                isinstance(probability, (int, float))
                and float(probability) >= float(minimum)
            ):
                return True

    minimum_count = rule.get("detection_count_gte")
    if minimum_count is not None:
        count = (result.get("model_output") or {}).get("detection_count")
        if isinstance(count, (int, float)) and count >= minimum_count:
            return True
    return False


def evaluate_policy(policy: dict, result: dict) -> str | None:
    has_evidence = (
        isinstance(result.get("predictions"), list)
        or "detection_count" in (result.get("model_output") or {})
    )
    if not has_evidence:
        return None
    matched = [
        rule["tier"]
        for rule in policy.get("rules") or []
        if rule.get("tier") in RISK_ORDER and _rule_matches(rule, result)
    ]
    if matched:
        return max(matched, key=RISK_ORDER.get)
    default = policy.get("default_tier")
    return default if default in RISK_ORDER else None


class RiskPolicyService:
    def __init__(self, database):
        self.departments = database["departments"]

    async def _policy(self, department: str, project: str) -> dict | None:
        document = await self.departments.find_one(
            {
                "departments": {
                    "$elemMatch": {
                        "department_name": department,
                        "projects": {"$exists": True},
                    }
                }
            }
        )
        if not document:
            return None
        for dept in document.get("departments") or []:
            if dept.get("department_name") != department:
                continue
            for model in (dept.get("projects") or {}).values():
                if (
                    model.get("project_name") == project
                    or model.get("model_name") == project
                ):
                    return model.get("risk_policy")
        return None

    async def assess(
        self,
        execution_steps: list[dict],
        step_results: list[dict],
    ) -> tuple[str | None, str]:
        result_by_step = {
            str(result.get("step_id")): result
            for result in step_results
        }
        tiers: list[str] = []
        for step in execution_steps:
            policy = await self._policy(step["department"], step["project"])
            if not policy:
                return None, "unavailable"
            result = result_by_step.get(str(step["step_id"]))
            if result is None:
                return None, "unavailable"
            tier = evaluate_policy(policy, result)
            if tier is None:
                return None, "unavailable"
            tiers.append(tier)
        if not tiers:
            return None, "unavailable"
        return max(tiers, key=RISK_ORDER.get), "assessed"
