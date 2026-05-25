import shutil, subprocess
from config import settings

def detect_compute_profile():
    if not settings.enable_cuda or not shutil.which('nvidia-smi'): return {'mode':'cpu','gpu':None}
    try:
        name=subprocess.check_output(['nvidia-smi','--query-gpu=name','--format=csv,noheader'], text=True, timeout=5).strip()
        return {'mode':'cuda','gpu':name}
    except Exception:
        return {'mode':'cpu','gpu':None}
