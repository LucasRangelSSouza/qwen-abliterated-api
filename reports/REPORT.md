# Test report

Endpoint: `https://qwen.rangeltech.net/v1` · started 2026-09-28T08:31:38-0300

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
| code | off | 1.46 | 39.2 | 39.0-39.3 | 0 |
| prose | off | 1.56 | 14.9 | 14.8-14.9 | 0 |
| sql | off | 1.87 | 33.5 | 33.4-33.6 | 0 |
| code | on | 1.54 | 27.1 | 26.8-27.4 | 0 |
| prose | on | 2.34 | 19.2 | 19.1-19.3 | 0 |
| sql | on | 1.73 | 22.0 | 21.9-22.1 | 0 |

## Heavy context (needle in a haystack, needle at 50%)

| target | prompt tokens | TTFT s | needle found |
|---|---:|---:|---|
| ~2000 | 1893 | 2.34 | yes |
| ~8000 | 7479 | 5.83 | yes |
| ~16000 | 14937 | 9.48 | yes |
| ~24000 | 22407 | 13.37 | yes |
| ~30000 | 27988 | 15.53 | NO  |

## Thinking on/off

- **think=False**: correct 3/3, reasoning chars [0, 0, 0], mean tokens 69, mean total 3.9 s
- **think=True**: correct 2/3, reasoning chars [207, 334, 212], mean tokens 103, mean total 4.5 s

Reasoning parser separates `reasoning` from `content`: **FAIL**

## Coding (10 tasks, code executed locally against asserts)

### think=False: 10/10

| task | pass | tokens | tok/s |
|---|---|---:|---:|
| fizzbuzz | yes | 81 | 40.6 |
| is_palindrome | yes | 39 | 51.2 |
| merge_intervals | yes | 104 | 46.1 |
| lru | yes | 160 | 42.1 |
| topo_sort | yes | 186 | 42.0 |
| roman | yes | 206 | 43.6 |
| wordfreq | yes | 90 | 49.0 |
| lis | yes | 75 | 49.3 |
| parse_duration | yes | 134 | 37.2 |
| matrix_spiral | yes | 217 | 47.8 |

### think=True: 9/10

| task | pass | tokens | tok/s |
|---|---|---:|---:|
| fizzbuzz | yes | 325 | 36.3 |
| is_palindrome | yes | 472 | 19.9 |
| merge_intervals | yes | 401 | 37.6 |
| lru | yes | 1698 | 26.1 |
| topo_sort | yes | 3341 | 22.7 |
| roman | yes | 639 | 34.8 |
| wordfreq | yes | 512 | 28.0 |
| lis | yes | 412 | 30.3 |
| parse_duration | yes | 509 | 23.8 |
| matrix_spiral | NO | 4000 | 25.1 |

## Tool calling

auto tool choice returns `get_weather(city=São Paulo)`: **PASS**

## Concurrency

| clients | ok | aggregate tok/s | per-request tok/s (mean) | TTFT s (mean) | wall s |
|---|---:|---:|---:|---:|---:|
| 1 | 1 | 22.9 | 26.2 | 2.29 | 17.5 |
| 2 | 2 | 45.9 | 25.9 | 2.01 | 17.4 |
| 4 | 4 | 83.7 | 24.2 | 1.72 | 19.1 |
| 8 | 8 | 87.7 | 25.0 | 10.14 | 36.5 |

## Long generation

3764 tokens, decode 19.1 tok/s, TTFT 1.87 s, finish `stop`

## Stability

60/60 requests OK (4 parallel), p50 4.13 s, p95 5.96 s

