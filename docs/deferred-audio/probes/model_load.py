import faulthandler, sys
faulthandler.enable()
print('step: import', flush=True)
from faster_whisper import WhisperModel
print('step: construct CPU int8', flush=True)
m = WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')
print('model loaded OK', flush=True)
