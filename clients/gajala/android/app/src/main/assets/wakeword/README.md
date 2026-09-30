Wake-word model for "Hey Gajala" (on-device, never uploaded).

Source: sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2 from the
k2-fsa/sherpa-onnx GitHub release "kws-models"
(sha256 68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6).
Files are the chunk-16 int8 encoder/joiner and fp32 decoder, renamed.

keywords.txt spells "Hey Gajala" as ARPAbet phonemes in five pronunciations.
Settings (threshold 0.3, boost 1.5, 3 trailing blanks) were chosen from
synthetic-voice tests: 29/30 detections on natural voices, 1 false trigger in
5.7 min of deliberately similar speech; accuracy drops in background noise.
