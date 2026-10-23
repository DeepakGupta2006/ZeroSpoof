import os
import sys
import tempfile
from pathlib import Path

# Isolated, throw-away state for every test run.
_tmp = tempfile.mkdtemp(prefix="zs-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["REDIS_URL"] = ""
os.environ["ZS_ENV"] = "dev"
os.environ["ZS_MODELS_DIR"] = f"{_tmp}/models"  # empty -> no ONNX models, default fusion weights
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
