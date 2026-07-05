# stt-cli

Cross-platform speech CLI for agent status updates.

Despite the repository name, this package currently focuses on text-to-speech
for orchestrator agents:

```bash
stt-say "Build finished, but the database migration test is failing."
stt-cli speak --backend kokoro --speaker af_heart "Review is blocked on an auth decision."
stt-say --level blocked --title "Blocked" --body "Need migration approval."
```

The command is intentionally thin. It does not redact, truncate, skip focus, or
decide when speech is important. That policy belongs to the orchestrator agent.
`--level` is accepted as caller metadata but is not interpreted by the CLI.

## Backends

- `vibevoice`: Uses Microsoft's official VibeVoice Realtime runtime for
  `microsoft/VibeVoice-Realtime-0.5B`. The public 1.5B checkpoint currently
  uses an unregistered `vibevoice` architecture and is reported as unsupported.
- `qwen3`: Uses Qwen3-TTS 0.6B. Defaults to
  `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` with speaker `Aiden`.
- `chatterbox`: Uses ResembleAI Chatterbox.
- `kokoro`: Uses the native Kokoro Python pipeline. Defaults to
  `hexgrad/Kokoro-82M` with voice `af_heart`.
- `neutts`: Uses NeuTTS Nano. Requires reference audio and transcript.
- `omnivoice`: Uses k2-fsa OmniVoice. Requires reference audio and transcript.
- `onnx`: Uses `sherpa-onnx` offline TTS over ONNX model families such as
  VITS/Piper, Kokoro, Matcha, and Kitten.
- `system`: Uses platform speech tools when available (`say` on macOS, SAPI on
  Windows, `spd-say`/`espeak`/`espeak-ng` on Linux).

The built-in default is Kokoro with `af_heart`, `speed = 1.25`, and
`device = auto`. `auto` remains available and tries VibeVoice first, then ONNX
if a model is configured, then system speech.

## Config

Most speech/model options can be set once in a config file. Explicit CLI flags
override config values, and built-in defaults apply when neither is provided.

Print the default config path:

```bash
stt-say --print-config-path
```

Default locations:

- macOS: `~/Library/Application Support/stt-cli/config.ini`
- Linux: `$XDG_CONFIG_HOME/stt-cli/config.ini` or `~/.config/stt-cli/config.ini`
- Windows: `%APPDATA%\stt-cli\config.ini`

Use `STT_CLI_CONFIG=/path/to/config.ini` or `--config /path/to/config.ini` to
choose a different file. Use `--no-config` to ignore config.

Example:

```ini
[speak]
backend = kokoro
speaker = af_heart
speed = 1.25
model_size = 0.5
device = auto
provider = auto
num_threads = 4
```

For ONNX models, the same model-path flags can be configured:

```ini
[speak]
backend = onnx
onnx_kind = kokoro
kokoro_model = /models/kokoro/model.onnx
kokoro_voices = /models/kokoro/voices.bin
kokoro_tokens = /models/kokoro/tokens.txt
kokoro_data_dir = /models/kokoro/espeak-ng-data
speaker = 1
speed = 1.05
```

## Install

```bash
python -m pip install -e .
```

Optional VibeVoice support:

```bash
python -m pip install -e ".[vibevoice]"
```

Optional model candidates for testing require Python 3.10+. They use separate
virtual environments because the upstream packages pin incompatible
`transformers` versions.

```bash
scripts/install-model-candidate-envs.sh
```

Optional ONNX support:

```bash
python -m pip install -e ".[onnx]"
```

## VibeVoice

```bash
stt-say --backend vibevoice --model-size 0.5 --speaker Emma "The orchestrator needs input."
```

The 0.5B model is the default because it is intended for real-time TTS. It uses
embedded voice prompts from the official demo; available built-in prompt names
are `Carter`, `Davis`, `Emma`, `Frank`, `Grace`, and `Mike`. You can also pass
`--speaker /path/to/prompt.pt`.

The public 1.5B checkpoint did not run in testing because the current public
runtime does not register its `vibevoice` architecture.

## Model Candidate Tests

The experimental candidates are explicit backends so installing one does not
surprise-change the default Samantha system voice.

```bash
scripts/benchmark-model-candidates.sh
```

Individual examples:

```bash
.venv-qwen3/bin/stt-cli benchmark --backend qwen3 --runs 2 \
  --speaker Aiden --language English \
  "Build finished. One migration test is failing and needs review."

.venv-chatterbox/bin/stt-cli benchmark --backend chatterbox --runs 2 \
  "Build finished. One migration test is failing and needs review."

.venv-kokoro/bin/stt-cli benchmark --backend kokoro --runs 2 \
  --language a --speaker af_heart \
  "Build finished. One migration test is failing and needs review."

.venv-vibevoice/bin/stt-cli benchmark --backend vibevoice --runs 2 \
  --model-size 0.5 \
  "Build finished. One migration test is failing and needs review."

.venv-vibevoice/bin/stt-cli benchmark --backend vibevoice --runs 2 \
  --model-size 1.5 \
  "Build finished. One migration test is failing and needs review."
# Currently expected to report an unsupported architecture error.
```

Reference-based models:

```bash
.venv-neutts/bin/stt-cli benchmark --backend neutts --runs 2 \
  --reference-audio ./reference.wav \
  --reference-text "Exact transcript of the reference clip." \
  "Build finished. One migration test is failing and needs review."

.venv-omnivoice/bin/stt-cli benchmark --backend omnivoice --runs 2 \
  --reference-audio ./reference.wav \
  --reference-text "Exact transcript of the reference clip." \
  "Build finished. One migration test is failing and needs review."
```

## ONNX / sherpa-onnx

For a Piper/VITS-style sherpa-onnx model:

```bash
stt-say --backend onnx \
  --onnx-kind vits \
  --vits-model ./vits-piper-en_US-lessac-medium/en_US-lessac-medium.onnx \
  --vits-tokens ./vits-piper-en_US-lessac-medium/tokens.txt \
  --vits-data-dir ./vits-piper-en_US-lessac-medium/espeak-ng-data \
  "The worker is ready for review."
```

For Kokoro:

```bash
stt-say --backend onnx \
  --onnx-kind kokoro \
  --kokoro-model ./kokoro/model.onnx \
  --kokoro-voices ./kokoro/voices.bin \
  --kokoro-tokens ./kokoro/tokens.txt \
  --kokoro-data-dir ./kokoro/espeak-ng-data \
  "The deployment gate is waiting."
```

Use `--output file.wav --no-play` to only write a WAV file.

## GPU / Accelerator Selection

Model backends default to accelerator auto-selection:

- VibeVoice: `device = auto` chooses CUDA when available, then Apple `mps`,
  then CPU. You can force `device = cpu`, `device = cuda`, `device = cuda:1`,
  or `device = mps`.
- ONNX/sherpa-onnx: `provider = auto` chooses Core ML on macOS, CUDA when
  `nvidia-smi` is present, then CPU. You can force `provider = cpu`,
  `provider = cuda`, or `provider = coreml`.

On Apple Silicon, VibeVoice uses PyTorch MPS for Metal GPU acceleration. ONNX
uses the Core ML provider, where the OS/runtime may schedule compatible work on
GPU or the Neural Engine depending on the model and provider support.

## Benchmark

Benchmark generation latency and live battery/power readings:

```bash
stt-cli benchmark --backend vibevoice --runs 3 \
  "Agent status benchmark. Build finished and review is waiting."
```

For ONNX:

```bash
stt-cli benchmark --backend onnx --runs 3 \
  --onnx-kind kokoro \
  --kokoro-model ./kokoro/model.onnx \
  --kokoro-voices ./kokoro/voices.bin \
  --kokoro-tokens ./kokoro/tokens.txt \
  "Agent status benchmark."
```

The benchmark writes temporary audio files and does not play them. It compares
CPU against the configured accelerator path. Wattage comes from live battery
telemetry when the operating system exposes it; on AC power those readings can
be noisy or unavailable.
