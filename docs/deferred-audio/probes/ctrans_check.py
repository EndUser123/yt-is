import traceback
for mod in ('ctranslate2', 'faster_whisper', 'torch'):
    try:
        m = __import__(mod)
        print(mod, 'OK', getattr(m, '__version__', '?'))
    except Exception:
        print(mod, 'IMPORT-FAIL')
        traceback.print_exc()
import os
print('cuda_visible:', os.environ.get('CUDA_VISIBLE_DEVICES'))
try:
    import torch
    print('torch cuda:', torch.cuda.is_available(), torch.__version__)
except Exception as e:
    print('torch check fail', e)
from pathlib import Path
hub = Path.home() / '.cache' / 'huggingface'
print('hf cache exists:', hub.exists())
if hub.exists():
    for p in hub.rglob('*large-v3-turbo*'):
        print('cached:', p)
        break
    else:
        print('no large-v3-turbo in cache')
