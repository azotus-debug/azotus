# Omega v8 Architecture Proposal: Batched Translation with Context Caching

**Date**: February 13, 2026
**Status**: PROPOSAL — Awaiting review
**Goal**: Reduce translation time from 20-45 min → 3-8 min, reduce cost from ~$9 → ~$3-5 per program

---

## The Problem

The current system makes **~220 individual API calls** to translate a 1-hour program. Each call:
- Sends the same system instruction (~1K tokens) — **paid 220 times**
- Sends the same Translation Brief (~2-4K tokens) — **paid 220 times**
- Burns 16,384 thinking tokens — **paid 220 times = 3.6M thinking tokens**
- Incurs network round-trip latency — **220 times**

This was designed when context windows were 8-32K tokens. Gemini 3 Pro has a **1M token context window**. A full 1-hour transcript is only ~15-20K tokens. The entire program fits comfortably in a single call.

---

## Current Architecture (v7)

```
Phase 0: Translation Brief (1 call, reads full transcript)
    ↓
Phase 1: Translate paragraphs (220 calls, waves of 8)
    Each call: system_instruction + brief + sliding_context + continuity + segments
    Each call: 16,384 thinking tokens
    ↓
Output: approved.json with all translated segments
```

**Timing**: 20-45 minutes
**Cost**: ~$9 per 1-hour program
**API calls**: ~221 (1 brief + 220 paragraphs)

---

## Proposed Architecture (v8): Chunked Translation with Context Caching

### Core Idea

Instead of 1 paragraph per API call, send **20-30 paragraphs per call** (5-8 minutes of content). This reduces 220 calls to **8-12 calls** while keeping chunks small enough for reliable structured output and checkpoint/resume.

### Why Not 1 Single Call?

- A single 200+ segment JSON response is fragile — any truncation loses everything
- No partial recovery — one failure means re-translating the entire program
- Thinking tokens would need to cover all segments at once (may be insufficient)
- Testing shows Gemini's attention to per-segment constraints (max_chars, CPS) degrades with very long arrays

### Why Not Keep 220 Calls with Caching?

Context caching would save ~$1-2 on the repeated system instruction, but:
- Still 220 network round-trips (latency bottleneck)
- Still 220 × thinking budget (the biggest cost)
- Still 20+ minutes wall-clock time

### The Sweet Spot: 8-12 Chunks

| Metric | v7 (220 calls) | v8 (10 chunks) | Improvement |
|--------|----------------|-----------------|-------------|
| API calls | 221 | 11 (1 brief + 10 chunks) | **20x fewer** |
| Thinking tokens | 220 × 16,384 = 3.6M | 10 × 16,384 = 164K | **22x fewer** |
| Network round-trips | 221 | 11 | **20x fewer** |
| Wall-clock time | 20-45 min | **3-8 min** | **5-10x faster** |
| Estimated cost | ~$9 | ~$3-5 | **~50% less** |

---

## Detailed Design

### Phase 0: Translation Brief (unchanged)

One Gemini call reads the full transcript and produces the structured brief. This already works well and costs ~$0.30-0.50. No changes needed.

### Phase 1: Chunked Translation

#### Chunk Assembly

```python
CHUNK_SIZE = 25  # paragraphs per chunk (tunable)

chunks = []
for i in range(0, len(paragraphs), CHUNK_SIZE):
    chunk_paragraphs = paragraphs[i:i+CHUNK_SIZE]
    chunk_segments = []
    for para in chunk_paragraphs:
        chunk_segments.extend(para.segments)
    chunks.append({
        'index': len(chunks),
        'paragraphs': chunk_paragraphs,
        'segments': chunk_segments,
        'start_time': chunk_paragraphs[0].start_time,
        'end_time': chunk_paragraphs[-1].end_time,
    })
```

For a typical 1-hour program with 220 paragraphs → **9 chunks** of ~25 paragraphs each.

#### Context Caching

Cache the fixed context once per job (valid for 1 hour):

```python
cache = client.caches.create(
    model='gemini-3-pro-preview',
    config=CreateCachedContentConfig(
        system_instruction=get_system_instruction(lang, profile, extra_terms),
        contents=[{
            'parts': [{'text': translation_brief}],
            'role': 'user'
        }, {
            'parts': [{'text': 'I understand the brief. Ready for translation chunks.'}],
            'role': 'model'
        }],
        ttl="3600s"
    )
)
```

Each chunk call references the cache — the system instruction + brief are NOT re-sent or re-charged at full price.

#### Per-Chunk Prompt

Each chunk receives:
1. **Chunk header**: "Translate chunk 3/9 (paragraphs 51-75, timestamps 12:30-18:45)"
2. **Continuity**: Last 5 translated segments from previous chunk (for flow)
3. **All segments in the chunk** as a JSON array with id, start, end, duration, max_chars, text, speaker
4. **Paragraph boundaries marked**: `--- PARAGRAPH BREAK (Speaker: Pastor John) ---` between groups
5. **Self-check rules** (same as current)

No sliding context window needed — the chunk IS the context. Each chunk covers ~5 min of content, giving Gemini natural awareness of what's being discussed.

#### Per-Chunk Response

Same JSON schema as current, but with more segments:

```json
{
  "segments": [
    {"id": "12345", "text": "Icelandic translation"},
    {"id": "12346", "text": "More translation"},
    ... (50-150 segments per chunk)
  ]
}
```

Typical chunk: 25 paragraphs × 5 segments = ~125 segments → ~4,000-6,000 output tokens. Well within 65K limit.

#### Chunk Wave Execution

```python
CHUNK_WAVE_SIZE = 3  # chunks in parallel (conservative)

for wave_start in range(0, len(chunks), CHUNK_WAVE_SIZE):
    wave_chunks = chunks[wave_start:wave_start+CHUNK_WAVE_SIZE]

    with ThreadPoolExecutor(max_workers=CHUNK_WAVE_SIZE) as pool:
        futures = {}
        for chunk in wave_chunks:
            if chunk['index'] in checkpoint:
                continue  # already translated
            future = pool.submit(translate_chunk, chunk, cache, continuity)
            futures[future] = chunk['index']

        for future in as_completed(futures):
            idx = futures[future]
            result = future.result()
            results[idx] = result
            save_checkpoint(results)  # after each chunk

    # Update continuity from completed chunks
    continuity = get_last_segments(results, count=10)
```

Only 3 chunks per wave (vs 8 paragraphs before) — this is conservative to avoid rate limits, but each chunk does 20x more work.

#### Checkpoint/Resume

Same principle as v7, but checkpointing per chunk instead of per paragraph:

```json
{
  "completed_chunks": {
    "0": [{"id": "12345", "text": "..."}, ...],
    "1": [{"id": "12400", "text": "..."}, ...],
  },
  "total_chunks": 9,
  "timestamp": "2026-02-13T14:23:45Z"
}
```

On restart: load checkpoint, skip completed chunks, resume from next.

---

## Thinking Budget Strategy

### Current (v7): 16,384 per paragraph × 220 = 3.6M thinking tokens

### Proposed (v8): Scaled per chunk

```python
# Gemini 3 Pro uses thinking_level instead of thinking_budget
# For Gemini 2.5 Pro fallback:
thinking_budget = 24576  # Higher per chunk (more segments to consider)
response_budget = max(4096, n_segments_in_chunk * 200)
max_output_tokens = thinking_budget + response_budget
```

Total thinking: 10 chunks × 24,576 = **245K tokens** (vs 3.6M in v7 = **15x reduction**)

### Gemini 3 Pro Preview

Uses `thinking_level` parameter (`low` or `high`) instead of `thinking_budget`:

```python
config = GenerateContentConfig(
    thinking_config=ThinkingConfig(thinking_level="high"),
    max_output_tokens=65536,  # Let Gemini manage the budget
)
```

This simplifies the thinking budget management — no more manual tuning.

---

## Model Choice: Gemini 3 Pro Preview

### Why Gemini 3 Pro (not 2.5 Pro, not Flash)

| Factor | 2.5 Pro | 3 Pro Preview | 2.5 Flash |
|--------|---------|---------------|-----------|
| Icelandic quality | Good | Better (improved reasoning) | Degraded |
| Thinking control | `thinking_budget` (manual) | `thinking_level` (automatic) | Optional |
| Price per 1M input | $1.25 | $2.00 | $0.30 |
| Price per 1M output | $10.00 | $12.00 | $2.50 |
| Cached input discount | 90% ($0.125/M) | 90% ($0.20/M) | 90% ($0.03/M) |
| Context window | 1M | 1M | 1M |
| Max output | 65K | 65K | 65K |

**Why not Flash**: Icelandic is a low-resource language. Research shows Pro significantly outperforms Flash for 78% of low-resource languages. For broadcast theology translation, Pro's quality advantage is worth the premium.

**Why 3 Pro over 2.5 Pro**: Better reasoning, improved instruction following (critical for strict glossary/CPS adherence), automatic thinking management. The ~60% price premium per token is offset by 22x fewer thinking tokens in the chunked architecture.

### v8 Cost Estimate with Gemini 3 Pro Preview

Per chunk (25 paragraphs, ~125 segments):
- Cached input (system + brief): ~5K tokens × $0.20/M = **$0.001** (negligible)
- Fresh input (chunk prompt): ~3K tokens × $2.00/M = **$0.006**
- Output: ~5K tokens × $12.00/M = **$0.060**
- Thinking: Managed by `thinking_level`, estimated ~8K tokens × rate TBD

Per job (10 chunks + 1 brief):
- Brief: ~$0.50
- Translation: 10 × ~$0.07-0.10 = ~$0.70-1.00
- **Total: ~$1.20-1.50 per program** ← vs $9 in v7

Even if thinking tokens double this estimate: **~$2-3 per program**.

---

## Eliminated Complexity

Things we can REMOVE in v8:

| Feature | Why It's No Longer Needed |
|---------|--------------------------|
| Sliding Context Window | The chunk IS the context (5 min of content visible at once) |
| Wave size tuning | Only 3-4 waves total instead of 28 |
| Per-paragraph timeout threads | Chunks are larger but fewer; standard API timeout suffices |
| 6-layer retry storms | 10 chunks × 3 retries = max 30 calls (vs 220 × 3 = 660) |
| Complex continuity buffer | Just pass last 5 segments from previous chunk |

---

## Failure Modes and Mitigations

| Failure | Impact in v7 | Impact in v8 | Mitigation |
|---------|-------------|-------------|------------|
| Single API timeout | Lose 1 paragraph, retry | Lose 1 chunk (~25 paragraphs), retry | Checkpoint per chunk |
| MAX_TOKENS crash | Lose 1 paragraph, retry | Lose 1 chunk, retry | Generous output budget; chunk size is tunable |
| Fragmentation bug | 910 paragraphs × $0.04 = $36 | 910/25 = 37 chunks × $0.10 = $3.70 | Circuit breaker still active |
| Retry storm (worst case) | 220 × 3 × 3 triggers = $594 | 10 × 3 × 3 triggers = $27 | Same guardrails, 20x less exposure |
| Total failure (re-translate) | $9 wasted | $1.50 wasted | Checkpoint means this rarely happens |

---

## Migration Path

### Phase 1: Chunk + Cache (v8-alpha)
- Refactor `omega_cloud_worker.py` wave loop to chunk-based
- Add context caching
- Keep Gemini 2.5 Pro as model (known quantity)
- Test on 2-3 programs, compare quality vs v7

### Phase 2: Gemini 3 Pro (v8-beta)
- Switch model to `gemini-3-pro-preview`
- Switch from `thinking_budget` to `thinking_level`
- Adjust prompts if needed for Gemini 3 behavior
- Test on same programs, compare quality

### Phase 3: Full Deployment (v8)
- Docker build + push + Cloud Run update
- Update guardrails for chunk-based cost estimation
- Production run

---

## Summary

| Metric | v7 (current) | v8 (proposed) |
|--------|-------------|---------------|
| API calls per program | ~221 | ~11 |
| Wall-clock translation time | 20-45 min | 3-8 min |
| Cost per program | ~$9 | ~$1.50-3.00 |
| Worst-case retry cost | ~$594 | ~$27 |
| Thinking tokens consumed | 3.6M | ~160-250K |
| Architecture complexity | High (6 retry layers, sliding context, waves of 8) | Low (simple chunk loop, context cache) |
| Checkpoint granularity | Per paragraph (220 checkpoints) | Per chunk (10 checkpoints) |
| Quality | Broadcast-grade | Same or better (more context per call) |

The key insight: **We were paying 220 separate "thinking fees" for a model that can easily think about 25 paragraphs at once.** That's like paying for 220 separate taxi rides instead of one bus.

---

## UPDATE: Gemini 3 Pro Feedback + Test Results (Feb 13, 2026)

### v8-alpha Test Run (25 paragraphs / 140 segments in one call)

**Results:**
- Model: `gemini-3-pro-preview`
- ✅ All 140/140 segment IDs returned perfectly
- ✅ Zero blank segments
- ⚠️ 32/140 CPS violations (23%) — constraint adherence degraded with many segments
- ⏱️ 124.2 seconds for one call (longer than expected)
- 💰 Token usage: 11,687 input, 4,512 output, 11,468 thinking = 27,667 total
  - vs v7 estimate: 25 × ~25K = 625K tokens → **22x token reduction confirmed**
- Translation quality: Solid — differences from v7 were natural variation, not errors

### Gemini 3 Pro's Own Architecture Recommendation

We consulted Gemini 3 Pro directly. Key feedback:

1. **Can it handle 1,600 segments (full program) in one call?** NO — not recommended for production.
   Output of ~48K tokens with strict per-segment constraints causes "attention fatigue" — dropped brackets,
   hallucinated IDs, blown character budgets by segment ~1,200.

2. **Practical safe zone**: **150-200 segments per generation** (~5K-10K output tokens) for flawless constraint execution.

3. **The optimal architecture (Gemini's recommendation)**: HYBRID approach:
   - **Send the ENTIRE transcript as input context** in every call (uses <5% of 1M context window)
   - **Chunk the output requests**: "Translate segments 1-200" per call
   - **Run chunks in parallel** — full context means no sequential dependencies
   - **Use Context Caching** — cache full transcript + system instruction, pay 90% less on input

4. **Prompt advice**: Strip it down. No "paragraphs", no "waves", no complex omission hierarchies.
   Just: (1) full English text with timings, (2) core Icelandic rules, (3) which segments to translate now.

### Revised v8 Architecture (Post-Gemini Feedback)

```
Step 1: Pre-segment transcript (existing pre_segmenter.py)
    ↓
Step 2: Create Context Cache:
    - System instruction (Icelandic rules, theology, tone)
    - Full transcript (all ~1,600 segments with timings)
    - Translation Brief (generated in same call or separate)
    Cache TTL: 1 hour
    ↓
Step 3: Parallel chunk translation (ALL chunks see full transcript):
    - Chunk 1: "Translate segments 1-200"    ─┐
    - Chunk 2: "Translate segments 201-400"   ─┤ All in parallel
    - Chunk 3: "Translate segments 401-600"   ─┤ (8 chunks for 1,600 segs)
    - ...                                     ─┤
    - Chunk 8: "Translate segments 1401-1600" ─┘
    Each references cached context (90% input discount)
    ↓
Step 4: Merge results → approved.json
```

**Expected performance:**
- 8 parallel API calls (vs 221 sequential)
- Wall-clock: ~2-3 minutes (single round of parallel calls)
- Cost: ~$1.50-3.00 per program (vs $9 in v7)
- Quality: Same or better (full sermon context in every call)

### Prompt Strategy (Clean v8 Prompt)

Gemini recommended: **ask Gemini to draft the prompt it wants to receive.**
Status: Pending — Gemini offered to draft a clean, condensed v8 prompt.
This should replace the current 700-line accumulated prompt with something focused:
1. The Icelandic translation rules (theology, grammar, tone) — system instruction
2. The segment range to translate with character budgets — user prompt
3. JSON response schema — config

### NEXT STEPS

1. [ ] Get Gemini's recommended v8 prompt draft
2. [ ] Test with 200 segments + full transcript context (cached)
3. [ ] If quality holds, implement full v8 pipeline
4. [ ] Docker build + deploy as v8
