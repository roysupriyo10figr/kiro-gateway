# Prompt Cache Verification

Verified on 2026-09-09 against the organization's Kiro runtime using the existing
SQLite-backed SSO authentication. No credentials or user prompt contents were
printed. All automated tests remain isolated from the network; the observations
below came from separate, synthetic live probes.

## Client Protocol

A local capture endpoint received a real Claude Code request with the
`prompt-caching-scope-2026-01-05` beta header and ephemeral markers in system
blocks and a conversation content block. Only selected protocol headers and
marker locations were inspected. Authorization values were not recorded.

This matches the provider-specific block metadata approach in the
[Vercel AI SDK prompt documentation](https://ai-sdk.dev/docs/foundations/prompts)
and [Claude Code's caching documentation](https://code.claude.com/docs/en/prompt-caching).
Kiro is not the Anthropic API: copying the client's beta headers would not
implement native checkpoint translation.

## Native Probes

Kiro CLI's installed SDK includes `cachePoint` fields on user and assistant
messages and a cache-point variant in the tool list. Live requests with empty
`cachePoint` objects on a message and in the tool list returned HTTP 200.

Synthetic repeated-prefix tests with Opus 5, low effort, and a brief requested
answer reported these credits:

| Experiment | First Request | Identical Repeat |
| --- | ---: | ---: |
| Message cache point | 0.140992 | 0.074732 |
| Cache point plus clientCacheConfig.useClientCachingOnly | 0.140964 | 0.074718 |
| No cache point, same clientCacheConfig | 0.140964 | 0.074718 |

Each experiment used a distinct prefix. The repeat reduction was approximately
47%, including in the control. This demonstrates a repeated-input metering
benefit, but does not establish that our explicit markers caused it. In
particular, the clientCacheConfig flag did not eliminate the benefit in this
probe, so the gateway does not use it to disable automatic backend behavior.

One pair's total latency fell from 4.42s to 2.69s; another was 2.73s versus 2.60s.
These are individual measurements, not a controlled latency benchmark.

The runtime returned credit metering and context usage, not explicit cache-read
or cache-write token counts. We cannot derive an exact cache hit rate, promise a
TTL, or identify the cause of an account-wide usage spike from these responses.
No cache token counters are synthesized from those measurements.

## Implemented Behavior

- Preserve client cache markers through Pydantic validation and conversation
  conversion, including tool-result repairs and adjacent-message merging.
- Map markers to enclosing Kiro message boundaries or tool checkpoints. System
  markers map to the first message containing the merged system prompt. Exact
  Anthropic block boundaries and TTLs are not representable by this mapping.
- Keep at most four recent checkpoints without changing user content.
- Preserve automatic backend caching when clients send no markers.
- Make checkpoint translation independently disableable with
  `KIRO_PROMPT_CACHE=false`.
- Log actual metered credits, upstream attempts, and response timing. Debug
  fingerprints identify changed checkpoint contents, not cache hits.

## Remaining Measurement Limits

### Model Coverage

Cache-marked live requests returned HTTP 200 for all 19 entries advertised by
the authenticated CLI: Auto; Opus 5, 4.8, 4.7, 4.6, and 4.5; Sonnet 5, 4.6,
4.5, and 4; Haiku 4.5; GPT 5.6 Sol, Terra, and Luna; DeepSeek 3.2; MiniMax
M2.5 and M2.1; GLM-5; and Qwen3-Coder-Next. Opus 5 and Sol were additionally
checked through both API formats in both response modes.

The offline matrix checks system, message, and tool cache translation for every
advertised model and a future unknown model ID, across both APIs and response
modes. Cache handling has no model allowlist. These checks establish request
compatibility, not a cache-hit guarantee or an identical discount across models.

Longer histories, more agent calls, retries, higher effort, changing system/tool
prefixes, and backend conditions can all affect total cost or latency. None is
proven to explain the reported surge. Compare matched workloads with actual
metering before attributing a saving to a gateway change. The independent Kiro
runtime still owns cache storage, eviction, account scope, and credit pricing.
