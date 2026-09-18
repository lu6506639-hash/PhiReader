"""Regression check for the desktop feedback link opener permission."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CAPABILITY = ROOT / "src-tauri" / "capabilities" / "default.json"


permissions = json.loads(CAPABILITY.read_text(encoding="utf-8"))["permissions"]

assert "opener:allow-open-url" in permissions
assert "opener:allow-default-urls" in permissions, (
    "the opener command permission alone does not allow https:// URLs"
)

print("Feedback opener permission check passed")
