# Test report

Endpoint: `https://qwen.rangeltech.net/v1` · started 2026-09-28T09:58:35-0300

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
| code | off | 1.37 | 34.8 | 34.6-34.9 | 0 |
| prose | off | 1.45 | 13.7 | 13.7-13.7 | 0 |
| sql | off | 3.01 | 38.2 | 36.9-39.1 | 0 |
| code | on | 1.81 | 28.0 | 27.6-28.5 | 0 |
| prose | on | 1.26 | 18.9 | 18.9-19.0 | 0 |
| sql | on | 1.29 | 19.9 | 19.8-20.1 | 0 |

## Heavy context (needle in a haystack, needle at 50%)

| target | prompt tokens | TTFT s | needle found |
|---|---:|---:|---|
| ~2000 | 2163 | 2.89 | 3/3 |
| ~8000 | 7749 | 7.36 | 3/3 |
| ~16000 | 15207 | 9.91 | 3/3 |
| ~24000 | 22677 | 17.50 | 3/3 |
| ~30000 | 28258 | 13.78 | 3/3 |

## Thinking on/off

- **think=False**: correct 3/3, reasoning chars [0, 0, 0], mean tokens 5, mean total 1.8 s
- **think=True**: correct 3/3, reasoning chars [951, 216, 173], mean tokens 158, mean total 7.1 s

Reasoning parser separates `reasoning` from `content`: **PASS**

## Coding (10 tasks, code executed locally against asserts)

### think=False: 9/10

| task | pass | tokens | tok/s |
|---|---|---:|---:|
| fizzbuzz | yes | 81 | 41.8 |
| is_palindrome | yes | 39 | 63.7 |
| merge_intervals | yes | 108 | 46.0 |
| lru | yes | 166 | 42.0 |
| topo_sort | NO | 190 | 37.8 |
| roman | yes | 198 | 39.7 |
| wordfreq | yes | 90 | 44.3 |
| lis | yes | 75 | 44.6 |
| parse_duration | yes | 107 | 34.7 |
| matrix_spiral | yes | 211 | 46.4 |

### think=True: 10/10

| task | pass | tokens | tok/s |
|---|---|---:|---:|
| fizzbuzz | yes | 281 | 31.1 |
| is_palindrome | yes | 294 | 29.9 |
| merge_intervals | yes | 612 | 24.6 |
| lru | yes | 621 | 34.0 |
| topo_sort | yes | 1585 | 21.6 |
| roman | yes | 812 | 26.3 |
| wordfreq | yes | 733 | 27.6 |
| lis | yes | 260 | 29.6 |
| parse_duration | yes | 893 | 29.5 |
| matrix_spiral | yes | 1002 | 27.2 |

## Tool calling

auto tool choice returns `get_weather(city=São Paulo)`: **PASS**

## Concurrency

| clients | ok | aggregate tok/s | per-request tok/s (mean) | TTFT s (mean) | wall s |
|---|---:|---:|---:|---:|---:|
| 1 | 1 | 31.2 | 38.5 | 1.58 | 8.3 |
| 2 | 2 | 22.0 | 38.2 | 8.92 | 23.5 |
| 4 | 4 | 98.6 | 30.9 | 1.69 | 10.5 |
| 8 | 8 | 107.2 | 32.7 | 6.87 | 19.5 |

## Long generation

4096 tokens, decode 18.5 tok/s, TTFT 1.99 s, finish `length`

## Prefix cache

Same 11476-token prompt twice: TTFT 8.82 s → 2.18 s: **PASS**

## Default thinking (client sends nothing)

reasoning present: **yes** (142 chars), TTFT 1.11 s, content starts `'\n\n144'`

## Stability

60/60 requests OK (4 parallel), p50 3.62 s, p95 6.56 s

