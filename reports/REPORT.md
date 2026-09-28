# Test report

Endpoint: `https://qwen.rangeltech.net/v1` · started 2026-09-28T09:09:34-0300

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
| code | off | 1.51 | 34.9 | 34.2-35.4 | 0 |
| prose | off | 2.54 | 13.0 | 12.9-13.0 | 0 |
| sql | off | 1.90 | 34.9 | 34.7-35.2 | 0 |
| code | on | 1.29 | 24.7 | 24.6-24.8 | 0 |
| prose | on | 1.53 | 17.6 | 17.3-17.9 | 0 |
| sql | on | 1.94 | 22.5 | 22.3-22.7 | 0 |

## Heavy context (needle in a haystack, needle at 50%)

| target | prompt tokens | TTFT s | needle found |
|---|---:|---:|---|
| ~2000 | 1893 | 2.50 | 3/3 |
| ~8000 | 7479 | 5.53 | 3/3 |
| ~16000 | 14937 | 7.38 | 3/3 |
| ~24000 | 22407 | 9.75 | 3/3 |
| ~30000 | 27988 | 12.38 | 3/3 |

## Thinking on/off

- **think=False**: correct 3/3, reasoning chars [0, 0, 0], mean tokens 62, mean total 4.5 s
- **think=True**: correct 3/3, reasoning chars [194, 489, 188], mean tokens 125, mean total 5.3 s

Reasoning parser separates `reasoning` from `content`: **PASS**

## Coding (10 tasks, code executed locally against asserts)

### think=False: 10/10

| task | pass | tokens | tok/s |
|---|---|---:|---:|
| fizzbuzz | yes | 101 | 48.9 |
| is_palindrome | yes | 39 | 39.3 |
| merge_intervals | yes | 108 | 50.9 |
| lru | yes | 159 | 38.8 |
| topo_sort | yes | 174 | 38.3 |
| roman | yes | 196 | 50.3 |
| wordfreq | yes | 90 | 45.3 |
| lis | yes | 75 | 39.3 |
| parse_duration | yes | 136 | 38.0 |
| matrix_spiral | yes | 211 | 50.7 |

### think=True: 10/10

| task | pass | tokens | tok/s |
|---|---|---:|---:|
| fizzbuzz | yes | 305 | 32.8 |
| is_palindrome | yes | 203 | 29.5 |
| merge_intervals | yes | 432 | 28.0 |
| lru | yes | 1203 | 24.3 |
| topo_sort | yes | 1317 | 24.5 |
| roman | yes | 998 | 33.3 |
| wordfreq | yes | 946 | 22.1 |
| lis | yes | 309 | 35.9 |
| parse_duration | yes | 699 | 20.1 |
| matrix_spiral | yes | 1039 | 35.7 |

## Tool calling

auto tool choice returns `get_weather(city=São Paulo)`: **PASS**

## Concurrency

| clients | ok | aggregate tok/s | per-request tok/s (mean) | TTFT s (mean) | wall s |
|---|---:|---:|---:|---:|---:|
| 1 | 1 | 22.5 | 30.3 | 4.61 | 17.8 |
| 2 | 2 | 47.5 | 28.7 | 2.40 | 16.8 |
| 4 | 4 | 78.5 | 23.4 | 2.38 | 20.4 |
| 8 | 8 | 90.5 | 24.2 | 9.89 | 35.3 |

## Long generation

4096 tokens, decode 22.9 tok/s, TTFT 1.15 s, finish `length`

## Prefix cache

Same 11206-token prompt twice: TTFT 7.85 s → 2.40 s: **PASS**

## Default thinking (client sends nothing)

reasoning present: **yes** (208 chars), TTFT 2.03 s, content starts `'\n\n144'`

## Stability

60/60 requests OK (4 parallel), p50 4.69 s, p95 6.6 s

