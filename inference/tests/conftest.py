import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="zs-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["REDIS_URL"] = ""
os.environ["ZS_ENV"] = "dev"
os.environ["ZS_MODELS_DIR"] = f"{_tmp}/models"  
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
