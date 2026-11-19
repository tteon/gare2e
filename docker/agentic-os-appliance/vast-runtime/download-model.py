import json
import os
from pathlib import Path
from huggingface_hub import snapshot_download

pin = json.loads(Path('/opt/gare2e-pilot/model-pin.json').read_text())
snapshot_download(repo_id=pin['repo'], revision=pin['revision'],
                  local_dir=os.environ.get('MODEL_PATH', '/models/qwen3.8-27b'),
                  allow_patterns=['*.json', '*.safetensors', '*.jinja', '*.txt', '*.model', '*.tiktoken'])
