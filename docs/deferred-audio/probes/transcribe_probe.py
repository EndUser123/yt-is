import faulthandler, sys, wave, struct
faulthandler.enable()
wav = r'C:\Users\brsth\AppData\Local\Temp\grok-goal-855c2bb71deb\implementer\silence.wav'
with wave.open(wav, 'wb') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(struct.pack('<8000h', *([0]*8000)))
print('wav written', flush=True)
from faster_whisper import WhisperModel
print('construct', flush=True)
m = WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')
print('transcribe', flush=True)
segs, info = m.transcribe(wav, language='en')
print('lang:', info.language, flush=True)
n = 0
for s in segs:
    n += 1
print('segments:', n, flush=True)
