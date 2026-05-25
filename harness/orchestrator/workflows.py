from pathlib import Path
import yaml

class WorkflowRegistry:
    def __init__(self):
        self.root = Path(__file__).resolve().parents[1] / 'workflows'
    def get(self, name):
        if not name:
            return None
        path = self.root / f'{name}.yaml'
        return yaml.safe_load(path.read_text()) if path.exists() else None
