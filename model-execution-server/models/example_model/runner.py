"""Example runner — template for model-specific inference logic.

Interface contract:
    predict(input_path: str, output_dir: str, config: dict) -> dict

The returned dict must be JSON-serializable.
"""
import json
import os
from pathlib import Path


def predict(input_path: str, output_dir: str, config: dict) -> dict:
    """Mock prediction — replace with real inference logic."""
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    output_path = Path(output_dir) / f"{input_file.stem}_result.json"
    result = {
        "model_name": config.get("model_name", "example_model"),
        "input_file": input_file.name,
        "prediction": "mock_label",
        "confidence": 0.99,
        "params_used": config.get("params", {}),
    }

    output_path.write_text(json.dumps(result, indent=2))

    return {
        "prediction": result["prediction"],
        "confidence": result["confidence"],
        "output_file": str(output_path),
    }
