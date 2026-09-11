import faulthandler, json
faulthandler.enable()
from pathlib import Path
groups = json.loads(Path('P:/tmp/audio-split.json').read_text())
files = sorted(groups['deferred_audio']['files'], key=lambda e: e['bytes'])
mid = [e for e in files if 8e6 <= e['bytes'] <= 12e6][0]
print('file:', mid['path'], flush=True)
from faster_whisper import WhisperModel
print('construct cpu int8', flush=True)
m = WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')
print('transcribe', flush=True)
segs, info = m.transcribe(mid['path'], language='en')
n = 0
for s in segs:
    n += 1
print('segments:', n, flush=True)
print('clean exit follows', flush=True)
