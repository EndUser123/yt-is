# Whisper fail-fast root cause (diagnose-only phase)

## Named root cause
CUDA context/DLL teardown abort on Windows after a fully completed
inference (faster-whisper 1.2.1 + ctranslate2 4.6.2 + torch cu128 nightly
on RTX 5070). Not an inference failure, not a model-load failure, not
missing CUDA.

## Evidence chain (all reproduced live this session)
1. Imports fine; torch.cuda True; large-v3-turbo cached (ctrans_check.py).
2. Model construction fine on CPU int8 and CUDA fp16 (model_load.py).
3. Silence transcribes cleanly on CPU and CUDA with exit 0
   (transcribe_probe.py, cuda_probe.py).
4. Real 8 MB deferred .mka on CUDA fp16: 114 segments materialize, then
   `Fatal Python error: Aborted` with no Python frame (mka_probe.py).
   Inference completes; the process dies in teardown.
5. Same file on CPU int8: 74 segments, clean exit 0 in 165 s (mka_cpu.py).
6. History agrees: transcript_attempts empty, 16 whisper artifacts ever.

## Fix applied in this goal (tolerance, not a C++ fix)
`parse_recovery_result` in scripts/run_visual_worker.py accepts a valid
result file despite a nonzero worker exit, so teardown aborts stop
discarding completed transcripts. Unit-tested (teardown-abort case).
The native teardown abort itself is NOT fixed; a proper fix is
os._exit(0) after result flush in csf/whisper_worker.py (out of scope)
or a CPU fallback retry.
