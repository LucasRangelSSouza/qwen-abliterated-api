# Test report

Endpoint: `https://qwen.example.com/v1` · started 2026-09-28T13:21:52-0300

## Authentication

| case | HTTP |
|---|---|
| no key | 401 |
| wrong key | 401 |
| right key | 200 |

Result: **PASS**

## Speed (streaming, 3 runs each, 700 max tokens)

| workload | thinking | TTFT s (mean) | decode tok/s (mean) | min-max tok/s | errors |
|---|---|---:|---:|---|---:|
| code | off | 1.40 | 33.4 | 33.1-33.7 | 0 |
| prose | off | 1.78 | 14.4 | 14.3-14.5 | 0 |
| sql | off | 1.79 | 36.0 | 35.0-36.6 | 0 |
| code | on | 2.42 | 22.1 | 22.0-22.1 | 0 |
| prose | on | 1.38 | 16.8 | 16.5-17.2 | 0 |
| sql | on | 1.77 | 24.6 | 24.2-25.1 | 0 |

## Heavy context (needle in a haystack, needle at 50%)

| target | prompt tokens | TTFT s | needle found |
|---|---:|---:|---|
| ~2000 | 2163 | 1.95 | 3/3 |
| ~8000 | 7749 | 5.11 | 3/3 |
| ~16000 | 15207 | 9.77 | 3/3 |
| ~24000 | 22677 | 14.87 | 3/3 |
| ~30000 | 28258 | 13.78 | 3/3 |

## Thinking on/off

- **think=False**: correct 3/3, reasoning chars [0, 0, 0], mean tokens 5, mean total 1.2 s
- **think=True**: correct 3/3, reasoning chars [201, 212, 187], mean tokens 76, mean total 4.2 s

Reasoning parser separates `reasoning` from `content`: **PASS**

## Coding (10 tasks, code executed locally against asserts)

### think=False: 9/10

| task | pass | tokens | tok/s |
|---|---|---:|---:|
| fizzbuzz | yes | 81 | 39.0 |
| is_palindrome | yes | 44 | 48.2 |
| merge_intervals | yes | 108 | 45.5 |
| lru | yes | 166 | 37.7 |
| topo_sort | NO | 190 | 36.6 |
| roman | yes | 196 | 40.9 |
| wordfreq | yes | 90 | 30.2 |
| lis | yes | 75 | 33.5 |
| parse_duration | yes | 107 | 37.8 |
| matrix_spiral | yes | 211 | 45.6 |

### think=True: 10/10

| task | pass | tokens | tok/s |
|---|---|---:|---:|
| fizzbuzz | yes | 257 | 32.5 |
| is_palindrome | yes | 215 | 30.5 |
| merge_intervals | yes | 436 | 33.2 |
| lru | yes | 500 | 36.1 |
| topo_sort | yes | 647 | 27.3 |
| roman | yes | 512 | 38.7 |
| wordfreq | yes | 1354 | 23.6 |
| lis | yes | 371 | 33.2 |
| parse_duration | yes | 322 | 27.3 |
| matrix_spiral | yes | 852 | 30.0 |

## Tool calling

auto tool choice returns `get_weather(city=São Paulo)`: **PASS**

## Concurrency

| clients | ok | aggregate tok/s | per-request tok/s (mean) | TTFT s (mean) | wall s |
|---|---:|---:|---:|---:|---:|
| 1 | 1 | 22.3 | 38.3 | 5.57 | 13.2 |
| 2 | 2 | 45.4 | 33.1 | 3.14 | 12.2 |
| 4 | 4 | 90.6 | 30.5 | 2.47 | 15.0 |
| 8 | 8 | 98.9 | 31.7 | 7.93 | 24.9 |

## Long generation

4096 tokens, decode 18.3 tok/s, TTFT 1.34 s, finish `length`

## Prefix cache

Same 11476-token prompt twice: TTFT 6.93 s → 3.60 s: **PASS**

## Default thinking (client sends nothing)

reasoning present: **yes** (138 chars), TTFT 1.46 s, content starts `'\n\n144'`

## Vision, audio and parallel questions

| check | result | detail |
|---|---|---|
| image_url (red square left, blue circle right) | PASS | On the left there is a red square, and on the right there is a blue circle. |
| audio input rejected cleanly, server keeps serving | PASS | HTTP 400 |
| speech-to-text sidecar `/v1/audio/transcriptions` (pt-BR fixture) | PASS | Qual é a capital do Brasil e quantos estados o país tem? (6.44 s) |
| 16 different questions at once, no cross-talk | PASS | 16/16 correct in 5.1 s |

## Stability

60/60 requests OK (4 parallel), p50 3.94 s, p95 5.48 s

## Idempotency

| step | result | seconds | detail |
|---|---|---:|---|
| baseline endpoint up + completion | PASS | None | pid=2784 |
| baseline whisper transcription through the public URL | PASS | None |  |
| configure.sh no-op when config unchanged | PASS | 4 | rc=0 nothing_to_do=True pid 2784->2784 weights_same=True |
| configure.sh leaves the whisper service untouched | PASS | None | whisper: service unchanged |
| hf download re-run moves no bytes | PASS | 3 |  |
| publish-endpoint.sh second run unchanged | PASS | None | edge: dynamic.yml unchanged |
| publish-endpoint.sh leaves exactly 5 managed blocks (no duplication) | PASS | None | blocks=5 |
| dns-upsert.sh already in place | PASS | None | dns: qwen.example.com already -> EDGE_IP |
| configure.sh repairs config drift and API returns | PASS | 426 | max_model_len restored=True |
| weights untouched by drift repair (no re-download) | PASS | None |  |
| hard restart #1: API back, no download, weights identical | PASS | 437 |  |
| hard restart #2: API back, no download, weights identical | PASS | 423 |  |
| Vast stop via API: endpoint goes down | PASS | 49 | {"success": true} |
| Vast start via API: same endpoint returns without redeploy | PASS | 472 | {"success": true} |
| weights survived stop/start | PASS | None |  |
| whisper sidecar came back by itself after the Vast start | PASS | 29 |  |

