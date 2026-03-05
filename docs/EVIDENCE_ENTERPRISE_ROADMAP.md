# Phương án triển khai Evidence theo chuẩn Enterprise

> Roadmap chuyển từ regex/heuristic sang LLM-driven, coverage-aware evidence handling.

---

## 1. Hiện trạng

### Đã có
| Component | Vai trò | Trạng thái |
|-----------|---------|------------|
| **Evidence Quality LLM** | `evaluate_quality_llm`, `evaluate_quality_llm_v2` | ✅ Có (config bật) |
| **Evidence Evaluator** | `evaluate_evidence` – relevance, retry suggestion | ✅ Có |
| **Evidence Set Builder** | `build_evidence_set` – primary/supporting, coverage | ⚠️ Heuristic |
| **Evidence Hygiene** | `compute_hygiene` – boilerplate, density | ✅ Logging |
| **format_evidence_for_prompt** | Chunk → prompt string | ✅ Đơn giản |

### Còn rule-based
| Component | Vấn đề |
|-----------|--------|
| `_chunk_satisfies_requirement` | Regex cố định cho transaction_link, policy, steps |
| `evaluate_quality` (regex path) | Feature scores bằng regex |
| Primary/supporting split | Top 3 = primary, còn lại = supporting (không coverage-aware) |
| Evidence selection | Rerank top-k cố định, không chọn theo requirement coverage |

---

## 2. Mục tiêu Enterprise

1. **LLM-first**: Evidence quality, coverage, selection đều qua LLM hoặc model.
2. **Coverage-aware selection**: Chọn evidence đảm bảo cover requirements, không chỉ top-k.
3. **Per-chunk relevance**: Mỗi chunk có score support query/claim.
4. **Structured evidence**: Metadata rõ ràng cho traceability.
5. **Graceful degradation**: Fallback regex khi LLM fail.

---

## 3. Roadmap theo Phase

### Phase 1: LLM Evidence Selector (2–3 tuần) ✅ ĐÃ TRIỂN KHAI

**Mục tiêu**: Thay top-k cố định bằng selection có ý thức coverage.

| Task | Mô tả | File | Status |
|------|-------|------|--------|
| 1.1 | Tạo `app/services/evidence_selector.py` | Mới | ✅ |
| 1.2 | LLM input: query, candidate chunks (top 15–20), required_evidence | - | ✅ |
| 1.3 | LLM output: `selected_chunk_ids`, `coverage_map`, `uncovered_requirements` | - | ✅ |
| 1.4 | Hàm `select_evidence_for_query()` → EvidenceSelectionResult | evidence_selector.py | ✅ |
| 1.5 | Trong retrieval: sau rerank, gọi selector trước build_evidence_set | retrieval.py | ✅ |
| 1.6 | Config `evidence_selector_use_llm`, `evidence_selector_fallback_top_k` | config.py | ✅ |

**Prompt mẫu**:
```
Given query and candidate chunks, select the minimal set that:
1. Covers all required_evidence (numbers, links, policy, steps)
2. Maximizes relevance to query
3. Avoids redundant chunks

Output JSON:
{
  "selected_chunk_ids": ["c1", "c2", ...],
  "coverage_map": {"numbers_units": "c1", "transaction_link": "c2", ...},
  "uncovered_requirements": [],
  "reasoning": "brief"
}
```

**Kết quả**: Evidence được chọn theo coverage, không chỉ theo score.

---

### Phase 2: LLM Per-Chunk Relevance (2 tuần)

**Mục tiêu**: Mỗi chunk có relevance score từ LLM (hoặc cross-encoder).

| Task | Mô tả | File |
|------|-------|------|
| 2.1 | Mở rộng EvidenceChunk: `relevance_score: float \| None`, `supports_claims: list[str]` | `search/base.py`, `schemas` |
| 2.2 | Option A: Batch LLM – 1 call đánh giá N chunks | `evidence_selector.py` hoặc mới |
| 2.3 | Option B: Cross-encoder reranker đã có – dùng score làm relevance | Đã có, chỉ map |
| 2.4 | Lưu relevance vào EvidenceChunk trước khi format prompt | `evidence_set_builder.py` |
| 2.5 | Prompt có thể sort/weight theo relevance | `format_evidence_for_prompt` |

**Kết quả**: Evidence có metadata relevance, hỗ trợ claim-level review.

---

### Phase 3: Coverage-Aware Evidence Set Builder (1–2 tuần) ✅ ĐÃ TRIỂN KHAI

**Mục tiêu**: Primary/supporting dựa trên coverage, không phải thứ tự.

| Task | Mô tả | File |
|------|-------|------|
| 3.1 | Thay `_chunk_satisfies_requirement` heuristic bằng LLM hoặc giữ làm fallback | `evidence_set_builder.py` |
| 3.2 | Primary chunks = chunks uniquely satisfy hard requirements | - |
| 3.3 | Supporting = chunks bổ sung relevance, không trùng coverage | - |
| 3.4 | `covered_requirements` từ LLM/selector output, không từ regex | - |
| 3.5 | EvidenceSet thêm `coverage_map: dict[str, str]` (req → chunk_id) | `schemas.py` |

**Kết quả**: EvidenceSet phản ánh đúng coverage thực tế.

---

### Phase 4: Evidence Quality LLM làm Default (1 tuần)

**Mục tiêu**: Ưu tiên LLM path, regex chỉ fallback.

| Task | Mô tả | File |
|------|-------|------|
| 4.1 | Config `evidence_quality_llm_default: bool = True` | `config.py` |
| 4.2 | Khi LLM fail → fallback regex, log warning | `evidence_quality.py` |
| 4.3 | Gộp Evidence Evaluator + Quality Gate: 1 LLM call cho cả relevance + pass/fail | Tùy chọn |
| 4.4 | Cache QualityReport theo (query_hash, chunk_ids_hash) | Redis/JSON |

**Kết quả**: Quality gate chủ yếu LLM, giảm phụ thuộc regex.

---

### Phase 5: Structured Evidence Format (1 tuần)

**Mục tiêu**: Prompt evidence có cấu trúc rõ, dễ trace.

| Task | Mô tả | File |
|------|-------|------|
| 5.1 | Format: `[Chunk {id}] (relevance: {score}) Source: ... Type: ... Content: ...` | `answer_utils.py` |
| 5.2 | Instruction: "Cite using chunk IDs. Do not cite chunks not listed." | System prompt |
| 5.3 | Evidence block có header: "Required: X, Y. Covered by: c1 (X), c2 (Y)." | - |
| 5.4 | Optional: JSON evidence block cho model hỗ trợ structured input | - |

**Kết quả**: Answer dễ map claim → chunk, phục vụ claim-level review.

---

### Phase 6: Loại bỏ / Thu nhỏ Regex (1–2 tuần)

| Task | Mô tả |
|------|-------|
| 6.1 | Giữ regex chỉ làm fallback khi LLM fail |
| 6.2 | Deprecate `_chunk_satisfies_requirement` – dùng coverage từ selector |
| 6.3 | Evidence hygiene: giữ cho logging; optional dùng classifier nhỏ thay regex |
| 6.4 | Document rõ fallback path |

---

## 4. Kiến trúc mới (sau Phase 5)

```
Rerank (cross-encoder)
        │
        ▼
┌─────────────────────┐
│ Evidence Selector   │  ← LLM: select chunks by coverage + relevance
│ (Phase 1)           │     Input: top 15–20, required_evidence
└──────────┬──────────┘     Output: selected chunks, coverage_map
           │
           ▼
┌─────────────────────┐
│ Evidence Set        │  ← Primary = coverage-unique; Supporting = rest
│ Builder (Phase 3)   │     coverage_map from selector
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Quality Gate        │  ← LLM (default), regex fallback
│ (Phase 4)           │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ Format for Prompt   │  ← Structured, relevance, coverage header
│ (Phase 5)           │
└─────────────────────┘
```

---

## 5. Config đề xuất

```python
# config.py
evidence_selector_use_llm: bool = True       # Coverage-aware selection
evidence_selector_fallback_top_k: int = 8    # Khi LLM fail
evidence_quality_llm_default: bool = True     # LLM first, regex fallback
evidence_quality_cache_enabled: bool = True   # Cache QualityReport
evidence_quality_cache_ttl_seconds: int = 3600
evidence_structured_format: bool = True       # Structured prompt block
```

---

## 6. Rủi ro & Giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|------------|
| Latency tăng (thêm LLM call) | Selector chạy song song với quality; cache |
| Cost tăng | Dùng economy model; cache; batch chunks |
| LLM chọn thiếu chunk quan trọng | Fallback top-k; A/B test |
| Regression | Giữ regex fallback; so sánh recall trước/sau |

---

## 7. Thứ tự triển khai gợi ý

1. **Phase 1** – Evidence Selector (impact lớn nhất)
2. **Phase 4** – LLM default cho quality (ít thay đổi flow)
3. **Phase 3** – Coverage-aware builder (dùng output selector)
4. **Phase 5** – Structured format
5. **Phase 2** – Per-chunk relevance (có thể dùng rerank score)
6. **Phase 6** – Dọn regex

---

## 8. Tích hợp với hiện tại

- **Evidence Evaluator** (`evaluate_evidence`): Giữ, dùng cho retry suggestion. Có thể gộp logic vào Evidence Selector (1 call thay 2).
- **Evidence Quality v2**: Giữ, làm default. Selector bổ sung selection; Quality Gate vẫn đánh giá pass/fail.
- **Retry Planner**: Vẫn dùng `missing_signals` từ Quality Report. Selector có thể cung cấp `uncovered_requirements` → map sang missing_signals.
