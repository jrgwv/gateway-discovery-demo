# Token comparison (us.amazon.nova-pro-v1:0)

| Query | Tools sent | Input tokens | vs. full catalog | Search latency |
|---|---|---|---|---|
| how many serious adverse event reports are there for semaglutide | 4 of 6 | 1005 (full: 1410) | -29% | 470 ms |
| find recruiting phase 3 trials for type 2 diabetes | 4 of 6 | 1101 (full: 1408) | -22% | 516 ms |
| has metformin been recalled | 4 of 6 | 1041 (full: 1401) | -26% | 452 ms |
| record a new safety signal for review | 4 of 6 | 994 (full: 1404) | -29% | 438 ms |

Measured: **174 tokens per tool schema** on average, plus a fixed **353 tokens** whenever any tools are sent.

Projection (not measured): tool tokens per request, assuming schemas the size of this repo's and 4.0 tools after search (the average sent above; capped at MAX_TOOLS=4). The search call itself adds one round trip per question.

| Catalog size | All tools in context | Search-scoped |
|---|---|---|
| 50 | 9,053 | 1,049 |
| 200 | 35,153 | 1,049 |
| 1000 | 174,353 | 1,049 |
