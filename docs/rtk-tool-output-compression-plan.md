# RTK-Style Tool Output Compression — Why It Makes Sense for Condense

> Status: **Planned** — to be implemented after multi-format API gateway (Phase 1).

---

## The Problem: Tool Outputs Dominate Token Spend in Coding Tools

When AI coding tools like Claude Code, Cursor, Codex, or Cline execute tool calls (read files, run commands, search code), the **tool output** is sent back to the LLM as context. These outputs are by far the largest token consumers:

| Tool Call | Typical Output Size | Frequency |
|-----------|-------------------|-----------|
| `cat large_file.py` | 5,000–20,000 tokens | Very high |
| `git diff` | 2,000–10,000 tokens | High |
| `grep -r "pattern" src/` | 1,000–15,000 tokens | High |
| `ls -la` / `find . -name "*.py"` | 500–3,000 tokens | Medium |
| `bash: test output` | 1,000–5,000 tokens | Medium |
| `tree` | 500–2,000 tokens | Low |

**In a typical 50-turn coding session, tool outputs account for 60–80% of input tokens.** The LLM doesn't need every line of a git diff or every match from grep to understand what happened — it needs the key information compacted.

---

## What Is RTK?

RTK (**R**educe **T**ool **K**it) is a technique pioneered by [9router](https://github.com/decolua/9router) that:

1. **Scans** messages for `role: "tool"` or `type: "tool_result"` content
2. **Auto-detects** what kind of output it is using regex heuristics
3. **Applies a domain-specific compaction filter** that preserves meaning while removing noise

### Auto-Detection Heuristics

RTK examines the first ~4KB of tool output and matches patterns:

```
Text starts with "diff --git"?     → Git diff filter
Text has porcelain-style status?   → Git status filter
Text has "npm ERR!" / "Compiling"? → Build output filter
Lines match "file:line:content"?   → Grep filter
All lines are path-like?           → Find filter
Contains box-drawing chars (├└)?   → Tree filter
Lines start with permissions?      → ls filter
Lines are numbered "  N|content"?  → Read-numbered (file content) filter
Many duplicate lines?              → Dedup log filter
Large unstructured blob?           → Smart truncate (head+tail)
```

### Domain-Specific Filters

Each filter knows the structure of its content type and compacts intelligently:

#### Git Diff → 50–80% savings
```
Before (raw diff, 847 lines):
diff --git a/src/pipeline/executor.py b/src/pipeline/executor.py
index abc1234..def5678 100644
--- a/src/pipeline/executor.py
+++ b/src/pipeline/executor.py
@@ -45,7 +45,7 @@ class PipelineExecutor:
-    def execute(self, context):
+    async def execute(self, context):
         steps = self._topo_sort()
... (800+ more lines of hunks)

After (compacted, 127 lines):
src/pipeline/executor.py
  @@ -45,7 +45,7 @@
  -    def execute(self, context):
  +    async def execute(self, context):
  ... (23 lines truncated)
  +12 -8

src/config/schema.py
  @@ -102,3 +102,15 @@
  +class CompressionConfig(BaseModel):
  +    enabled: bool = False
  ... (4 lines truncated)
  +15 -0
```

The LLM gets: which files changed, what the key changes are, and summary stats. It doesn't need 800 lines of context around the changes.

#### Grep → 40–60% savings
```
Before:
src/cache/base.py:15:class CacheBackend(ABC):
src/cache/base.py:22:    async def get(self, key):
src/cache/base.py:28:    async def set(self, key, value, ttl):
src/cache/memory.py:8:class InMemoryCache(CacheBackend):
src/cache/memory.py:15:    async def get(self, key):
src/cache/memory.py:22:    async def set(self, key, value, ttl):
src/cache/redis_backend.py:10:class RedisCache(CacheBackend):
src/cache/redis_backend.py:18:    async def get(self, key):
src/cache/redis_backend.py:25:    async def set(self, key, value, ttl):

After:
src/cache/base.py
  :15: class CacheBackend(ABC):
  :22:     async def get(self, key):
  :28:     async def set(self, key, value, ttl):
src/cache/memory.py
  :8: class InMemoryCache(CacheBackend):
  :15:     async def get(self, key):
  :22:     async def set(self, key, value, ttl):
src/cache/redis_backend.py
  :10: class RedisCache(CacheBackend):
  :18:     async def get(self, key):
  :25:     async def set(self, key, value, ttl):
```

File paths are deduplicated instead of repeated on every line.

#### File Content (Read-Numbered) → 30–70% savings
```
Before (500-line file dump):
   1│import os
   2│import sys
   3│import json
   ...
 245│    def process(self, data):
 246│        # Main processing logic
 247│        results = []
   ...
 498│if __name__ == "__main__":
 499│    main()
 500│

After (head + relevant section + tail):
   1│import os
   2│import sys
   3│import json
   ... (lines 4-243 omitted, 240 lines)
 245│    def process(self, data):
 246│        # Main processing logic
 247│        results = []
   ... (lines 248-496 omitted, 249 lines)
 498│if __name__ == "__main__":
 499│    main()
 500│
```

---

## Why RTK Makes Sense for Condense

### 1. It Fills the Gap in Our Compression Stack

Our current compression backends are general-purpose:

| Backend | Strength | Weakness for Tool Outputs |
|---------|----------|--------------------------|
| **FusionEngine** | Code/JSON structural compression | Doesn't know git diff structure, compresses all code equally |
| **LLMLingua** | Natural language via BERT importance scoring | Designed for prose, not structured CLI output |

RTK is **domain-specific** — it knows that a git diff can be compacted by keeping hunks + stats, that grep results can deduplicate file paths, that file dumps can keep head + tail. This structural knowledge is what delivers 20–40% savings on top of what generic compressors achieve.

### 2. It Targets the #1 Token Consumer

For our target users (Claude Code, Cursor, Codex), tool outputs are 60–80% of tokens:

```
Typical coding session token breakdown:
├── System prompt:     5%    ← LLMLingua can help
├── User messages:    10%    ← Already small
├── Assistant msgs:   15%    ← Can't compress (output)
└── Tool outputs:     70%    ← RTK's sweet spot ★
```

A 30% reduction on 70% of tokens = **21% total session savings** from RTK alone.

### 3. It Fits Our Architecture Perfectly

RTK would be a `CompressionBackend` in our existing registry:

```python
# condense/compression/backends/tool_output_backend.py

@compression_registry.register("tool_output")
class ToolOutputCompressionBackend(CompressionBackend):
    """Auto-detect and compact tool_result content in messages.
    
    Targets coding tool outputs: git diff, grep, ls, file reads, 
    build logs, etc. Uses domain-specific filters for structural
    compression that preserves meaning.
    """
    
    @property
    def available(self) -> bool:
        return True  # Pure Python, zero dependencies
    
    def compress_messages(self, messages: list[dict]) -> CompressResult:
        original_tokens = 0
        compressed_tokens = 0
        
        for msg in messages:
            if not self._is_tool_output(msg):
                continue
            
            content = self._extract_content(msg)
            if len(content) < MIN_COMPRESS_SIZE:
                continue
            
            filter_fn = autodetect(content)
            if filter_fn is None:
                continue
            
            compressed = filter_fn(content)
            if len(compressed) < len(content):
                self._set_content(msg, compressed)
                original_tokens += count_tokens(content)
                compressed_tokens += count_tokens(compressed)
        
        return CompressResult(
            messages=messages,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            reduction_pct=...
        )
```

**Zero core edits needed.** Just create the file, decorate with `@register`, done.

### 4. Zero Dependencies

Unlike FusionEngine (requires `claw-compactor`) or LLMLingua (requires `llmlingua` + BERT model), RTK is **pure Python/regex**. No ML models, no pip packages, no downloads. It works immediately on any system.

### 5. It Stacks with Other Compression

Users can enable multiple compression backends:

```yaml
optimizations:
  - id: "compression"
    type: "compression"
    enabled: true
    config:
      # Phase 1: Compress tool outputs (git diff, grep, ls, etc.)
      compressor_type: "tool_output"
      
      # OR Phase 2: Chain multiple compressors
      # chain: ["tool_output", "fusion"]
      # → First compact tool outputs structurally
      # → Then compress remaining code/JSON with FusionEngine
```

### 6. It's the Highest ROI for Our Target Users

| Optimization | Savings | Effort | Dependencies |
|-------------|---------|--------|-------------|
| Exact cache | 100% on hits (but only identical requests) | Already built | None |
| Semantic cache | 100% on hits (similar requests) | Already built | sentence-transformers |
| ML routing | Variable (cheaper model for simple queries) | Already built | routellm |
| LLMLingua compression | 50-70% on natural language | Already built | llmlingua + BERT |
| FusionEngine compression | 30-40% on code | Already built | claw-compactor |
| **RTK tool output compression** | **20-40% on 70% of tokens** | **To build** | **None** |

RTK delivers the best cost-savings-per-engineering-hour for coding tool users because it targets the largest token category with zero runtime dependencies.

---

## Implementation Plan

### Proposed Architecture

```
condense/compression/backends/
├── fusion_backend.py          (existing)
├── llmlingua_backend.py       (existing)
└── tool_output/               (NEW)
    ├── __init__.py
    ├── backend.py             # ToolOutputCompressionBackend
    ├── autodetect.py          # Pattern detection heuristics
    └── filters/
        ├── __init__.py
        ├── git_diff.py        # Hunk compaction + stats summary
        ├── git_status.py      # Porcelain compaction
        ├── grep.py            # Path deduplication
        ├── ls.py              # ls -la compaction
        ├── tree.py            # Tree depth trimming
        ├── find.py            # Path prefix deduplication
        ├── build_output.py    # Build log noise removal
        ├── file_content.py    # Head + tail preservation
        ├── dedup_log.py       # Duplicate line removal
        └── smart_truncate.py  # Generic head + tail fallback
```

Each filter is a self-contained function:
```python
def git_diff(text: str, max_hunk_lines: int = 100) -> str:
    """Compact unified diff: keep file headers, truncate hunks, summarize +/-."""
    ...
```

### Filter Registry (Extensible)

Filters themselves use the registry pattern, so contributors can add new filters:

```python
from condense.compression.backends.tool_output.autodetect import register_filter

@register_filter(
    name="git_diff",
    detect=lambda head: bool(re.search(r'^diff --git ', head, re.M)),
    priority=10,  # higher = checked first
)
def git_diff(text: str, **config) -> str:
    ...
```

### Configuration

```yaml
optimizations:
  - id: "compression"
    type: "compression"
    enabled: true
    config:
      compressor_type: "tool_output"
      tool_output:
        # Which filters to enable (default: all)
        filters:
          git_diff: { enabled: true, max_hunk_lines: 100 }
          grep: { enabled: true }
          file_content: { enabled: true, max_head_lines: 50, max_tail_lines: 20 }
          smart_truncate: { enabled: true, max_lines: 500 }
        # Minimum content size to attempt compression (bytes)
        min_size: 512
        # Maximum content size to process (skip very large blobs)
        max_size: 1048576  # 1MB
```

### Estimated Effort

| Task | Time |
|------|------|
| Backend class + autodetect engine | 1 day |
| Port core filters (git_diff, grep, file_content, smart_truncate) | 1.5 days |
| Port remaining filters (ls, tree, find, build_output, dedup_log, git_status) | 1 day |
| Unit tests for each filter | 1 day |
| Integration tests (pipeline + E2E) | 0.5 day |
| **Total** | **~5 days** |

### Prerequisites

- **Phase 1 (Multi-Format API Gateway)** should be done first — without Claude Code / Cursor integration, there are no tool outputs to compress
- Once tools are connected, tool output compression becomes immediately testable via dogfooding

---

## Comparison: Our Approach vs. 9router RTK

| Aspect | 9router RTK | Condense Tool Output Backend |
|--------|------------|------------------------------|
| **Language** | JavaScript | Python |
| **Architecture** | Hardcoded in translation pipeline | Registry-based `CompressionBackend` |
| **Runs when** | Before format translation, always on | Configurable — enable/disable per config |
| **Stacks with other compression** | No | Yes — can chain with FusionEngine/LLMLingua |
| **Filter extensibility** | Add JS file, import in index.js | `@register_filter()` decorator, zero core edits |
| **Configuration** | On/off toggle only | Per-filter thresholds, max lines, min size |
| **Metrics** | Byte-level stats only | Full token counting + USD savings in dashboard |
| **Per-tool tracking** | No | Yes — "Claude Code tool outputs: saved $X" |
| **Testing** | Minimal | Full unit + integration + E2E |

---

## References

- [9router RTK source](https://github.com/decolua/9router/tree/main/open-sse/rtk) — JavaScript implementation
- [9router README](https://github.com/decolua/9router) — "Save 20-40% tokens with RTK"
- Claude Code tool output patterns — `tool_result` blocks with `content` arrays
- OpenAI tool message patterns — `role: "tool"` with string `content`
