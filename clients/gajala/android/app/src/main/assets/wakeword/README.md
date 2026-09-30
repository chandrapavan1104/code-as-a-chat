Wake-word model for "Hey Gajala" (on-device, never uploaded).

Source: sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2 from the
k2-fsa/sherpa-onnx GitHub release "kws-models"
(sha256 68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6).
Files are the chunk-16 int8 encoder/joiner and fp32 decoder, renamed.

keywords.txt spells "Hey Gajala" as ARPAbet phonemes in eleven pronunciations:
five "ga-JAH-la" forms, plus six with a stressed "ZIL"/"ZEL"/"JIL" middle
syllable. The owner's real attempts were transcribed by Google as "Hey
Godzilla", "Gisela", "gazella" and "Coachella"; the original five caught 0/30
synthetic clips of that style, the combined set 17-18/30 (and still 28/30 of
"ga-JAH-la"). Settings: threshold 0.3, boost 1.5, 3 trailing blanks; ~2 false
triggers in 6.9 min of deliberately similar speech. Accuracy drops in noise.
