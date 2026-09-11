import faulthandler
faulthandler.enable()
wav = r'C:\Users\brsth\AppData\Local\Temp\grok-goal-855c2bb71deb\implementer\silence.wav'
from faster_whisper import WhisperModel
print('construct cuda fp16', flush=True)
m = WhisperModel('large-v3-turbo', device='cuda', compute_type='float16')
print('transcribe', flush=True)
segs, info = m.transcribe(wav, language='en')
n = 0
for s in segs:
    n += 1
print('segments:', n, flush=True)
