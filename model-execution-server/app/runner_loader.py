"""Runner 동적 로더.

각 모델 폴더의 runner.py 를 importlib 으로 동적 로드하고
predict(input_path, output_dir, config) 를 호출합니다.
"""
import importlib.util
import sys
from pathlib import Path
from typing import Any


def load_runner(config: dict[str, Any]):
    """config 의 runner_path 를 읽어 모듈로 반환."""
    runner_path = Path(config["runner_path"])
    if not runner_path.exists():
        raise FileNotFoundError(f"Runner not found: {runner_path}")

    module_name = f"_runner_{config.get('model_name', 'unknown')}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, runner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load runner: {runner_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def run_predict(config: dict[str, Any], input_path: str, output_dir: str) -> dict[str, Any]:
    """runner.predict() 호출 후 결과 dict 반환."""
    runner = load_runner(config)
    if not hasattr(runner, "predict"):
        raise AttributeError(f"runner.py must implement predict(input_path, output_dir, config)")
    result = runner.predict(input_path, output_dir, config)
    if not isinstance(result, dict):
        raise TypeError(f"runner.predict() must return dict, got {type(result)}")
    return result
