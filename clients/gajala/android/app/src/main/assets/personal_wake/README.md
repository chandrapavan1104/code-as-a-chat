# Gajala speech features

The feature-only models are from [openWakeWord v0.5.1](https://github.com/dscripka/openWakeWord/releases/tag/v0.5.1).
See the upstream [model architecture and attribution](https://github.com/dscripka/openWakeWord#model-architecture)
for the mel frontend and Google Speech Embedding backbone. This bundle uses
neither openWakeWord's wake-word classification heads nor any owner's audio.

| File | SHA-256 |
|---|---|
| `melspectrogram.tflite` | `96fa0adccb6e8cf95cb14465409a1a2898ee4a96a85bb9ed3c7eb0e68bf163e8` |
| `embedding_model.tflite` | `c0aea21eb84a4ce90a08c870da41b7a7173b45269e6a3207c71d67c40f3a59d8` |

The 0.93 initial cosine threshold was checked with the exact TFLite assets on
a Mac. Separate Samantha and Rishi synthetic profiles used three recordings
of “Gajala”. Four evaluation variants per phrase included different speaking
rates and one with added noise. The lowest target score was 0.9531 and the
highest near-match score was 0.8983 (“Godzilla”). This is a development check,
not a measurement of real speech accuracy or false wakes per hour.

Device acceptance: compare 20 fresh quiet/noisy attempts with the legacy and
personalized configurations, record detection delay and battery, and run two
hours of ordinary speech and media. Personalized detection requires an
unlocked, interactive phone and a short pause after the wake word.
