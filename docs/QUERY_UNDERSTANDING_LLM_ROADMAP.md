# Phương hướng phát triển: Query Understanding / Rewriting bằng LLM

> Roadmap chuyển từ rule-based sang LLM-driven cho query understanding và retrieval rewriting.

---

## 1. Hiện trạng

### Đã có LLM
| Component | Vai trò | Trạng thái |
|-----------|---------|------------|
| **Normalizer** | Sinh QuerySpec (keyword_queries, semantic_queries, retrieval_profile, intent, ...) | ✅ LLM |
| **Evidence Evaluator** | Đánh giá evidence, sinh `suggested_query` khi retry | ✅ LLM |

### Còn rule-based
| Component | Vai trò | File |
|-----------|---------|------|
| **Retrieval Planner** | `_resolve_profile`, `_resolve_queries`, `_derive_doc_types` khi QuerySpec thiếu/không đủ | `retrieval_planner.py` |
| **Retrieval Service** | `_query_rewrite` fallback, `_resolve_retrieval_profile`, `_is_pricing_retrieval`, `_derive_ensure_doc_types` | `retrieval.py` |
| **Retry Planner** | `MISSING_SIGNAL_STRATEGIES` → boost_patterns, filter_doc_types | `retry_planner.py` |
| **Answer Utils** | `_pick_intent_aligned_rewrite` (policy_terms, drift_terms, step_terms) | `answer_utils.py` |

---

## 2. Mục tiêu

1. **Giảm rule cứng**: Thay thế keyword lists và if/else bằng LLM.
2. **Tăng coverage**: QuerySpec luôn có đủ thông tin → ít fallback.
3. **Retry thông minh hơn**: LLM sinh retry query/strategy thay vì rule.
4. **Dễ mở rộng**: Thêm intent/profile mới không cần sửa code.

---

## 3. Roadmap theo phase

### Phase 1: Tăng độ tin cậy QuerySpec (1–2 tuần)

**Mục tiêu**: QuerySpec luôn có đủ dữ liệu → retrieval không cần fallback rule.

| Task | Mô tả | File |
|------|-------|------|
| 1.1 | Bổ sung `retrieval_profile` vào Normalizer prompt nếu chưa rõ | `normalizer.py` |
| 1.2 | Bổ sung `ensure_doc_types` / `preferred_doc_types` vào QuerySpec schema | `schemas.py`, `normalizer.py` |
| 1.3 | Đảm bảo `keyword_queries`, `semantic_queries`, `rewrite_candidates` luôn có ít nhất 1 phần tử | `normalizer.py` |
| 1.4 | Thêm config `query_spec_required` – khi True, bắt buộc có QuerySpec trước retrieval | `config.py`, `orchestrator.py` |
| 1.5 | Khi Normalizer fail → gọi LLM fallback nhẹ (chỉ query rewrite) thay vì `_build_minimal_fallback` | `normalizer.py` |

**Kết quả**: Retrieval Planner và Retrieval Service ưu tiên QuerySpec, ít khi chạy rule fallback.

---

### Phase 2: LLM Query Rewriter độc lập (2–3 tuần) ✅ ĐÃ TRIỂN KHAI

**Mục tiêu**: Service riêng cho query rewriting, dùng khi không có QuerySpec hoặc cần rewrite lại.

| Task | Mô tả | File | Status |
|------|-------|------|--------|
| 2.1 | Tạo `app/services/query_rewriter.py` | Mới | ✅ |
| 2.2 | Prompt: input = (query, conversation_history, retry_context), output = (keyword_query, semantic_query, retrieval_profile) | `query_rewriter.py` | ✅ |
| 2.3 | Hàm `rewrite_for_retrieval(query, ...) -> QueryRewriteResult` | `query_rewriter.py` | ✅ |
| 2.4 | Trong `RetrievalService._query_rewrite`: khi không có QuerySpec → gọi `query_rewriter.rewrite_for_retrieval` thay vì heuristic | `retrieval.py` | ✅ |
| 2.5 | Config `query_rewriter_use_llm`, `query_rewriter_cache_enabled`, `query_rewriter_cache_ttl_seconds` | `config.py` | ✅ |
| 2.6 | Redis cache theo query_hash (Phase 2.2) | `query_rewriter.py` | ✅ |

**Prompt mẫu**:
```
You are a query rewriter for a support RAG system.
Input: user query, optional conversation context.
Output JSON:
{
  "keyword_query": "optimized phrase for BM25 (include key terms, synonyms)",
  "semantic_query": "natural phrase for vector search",
  "retrieval_profile": "pricing_profile|policy_profile|troubleshooting_profile|generic_profile"
}
Rules: keyword_query should favor exact-match terms; semantic_query can be more natural.
```

**Kết quả**: Fallback không còn rule-based, dùng LLM thống nhất.

---

### Phase 3: LLM Retry Strategy (2–3 tuần)

**Mục tiêu**: Retry strategy do LLM sinh từ missing_signals và evidence.

| Task | Mô tả | File |
|------|-------|------|
| 3.1 | Mở rộng Evidence Evaluator: thêm output `retry_strategy` (suggested_query, boost_terms, filter_doc_types, exclude_patterns) | `evidence_evaluator.py` |
| 3.2 | Hoặc tạo `RetryStrategyPlanner` LLM riêng: input = (query, missing_signals, evidence_summary), output = RetryStrategy | `retry_planner.py` |
| 3.3 | Trong `plan_retry`: ưu tiên LLM output, fallback `MISSING_SIGNAL_STRATEGIES` | `retry_planner.py` |
| 3.4 | Config `retry_planner_use_llm` | `config.py` |

**Prompt mẫu**:
```
Evidence is missing: [missing_numbers, missing_links].
Query: "VPS plans price"
Current evidence summary: ...

Suggest retry strategy. Output JSON:
{
  "suggested_query": "alternative query for retry, or null to keep original",
  "boost_terms": ["USD", "monthly", "pricing"],
  "filter_doc_types": ["pricing"],
  "exclude_patterns": []
}
```

**Kết quả**: Retry không còn phụ thuộc rule `MISSING_SIGNAL_STRATEGIES`.

---

### Phase 4: Loại bỏ rule fallback (1–2 tuần)

**Mục tiêu**: Xóa hoặc thu nhỏ các rule còn lại.

| Task | Mô tả | File |
|------|-------|------|
| 4.1 | Xóa `_resolve_profile` rule fallback – luôn dùng QuerySpec.retrieval_profile hoặc QueryRewriter | `retrieval_planner.py` |
| 4.2 | Xóa `_resolve_retrieval_profile`, `_is_pricing_retrieval` rule – dùng profile từ LLM | `retrieval.py` |
| 4.3 | Xóa `_derive_ensure_doc_types` rule – dùng ensure_doc_types từ QuerySpec | `retrieval.py`, `retrieval_planner.py` |
| 4.4 | Xóa heuristic expansion trong `_query_rewrite` (extras: pricing, budget, USD, ...) | `retrieval.py` |
| 4.5 | Xóa `_pick_intent_aligned_rewrite` rule – dùng rewrite_candidates từ LLM | `answer_utils.py` |
| 4.6 | Giữ `MISSING_SIGNAL_STRATEGIES` chỉ làm fallback khi LLM fail (optional) | `retry_planner.py` |

**Kết quả**: Luồng retrieval gần như hoàn toàn LLM-driven.

---

### Phase 5: Tối ưu & mở rộng (ongoing)

| Task | Mô tả |
|------|-------|
| 5.1 | Cache QuerySpec / QueryRewrite theo query hash để giảm LLM calls |
| 5.2 | Dùng model nhỏ (e.g. 7B) cho query rewriter nếu latency quan trọng |
| 5.3 | A/B test: rule vs LLM để đo recall, precision, latency |
| 5.4 | Fine-tune model nhỏ trên domain nếu có đủ labeled data |

---

## 4. Kiến trúc mới (sau Phase 4)

```
User Query
    │
    ▼
┌─────────────────┐
│   Normalizer    │  ← LLM: QuerySpec (keyword_queries, semantic_queries, retrieval_profile, ...)
└────────┬────────┘
         │
         ▼
┌─────────────────┐     QuerySpec missing?
│ Retrieval Plan  │ ──────────────────────► QueryRewriter (LLM)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Retrieval    │  ← BM25(keyword_query) + Vector(semantic_query)
└────────┬────────┘
         │
         ▼
┌─────────────────┐     attempt=2, missing_signals?
│ Evidence Eval   │ ──────────────────────► RetryStrategyPlanner (LLM)
└────────┬────────┘
         │
         ▼
    Answer / Retry
```

---

## 5. Config đề xuất

```python
# config.py
query_rewriter_use_llm: bool = True      # Fallback query rewrite bằng LLM
query_spec_required: bool = False        # Bắt buộc QuerySpec trước retrieval (strict mode)
retry_planner_use_llm: bool = True       # Retry strategy từ LLM
retry_rule_fallback: bool = True         # Giữ rule fallback khi LLM fail
```

---

## 6. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|------------|
| Latency tăng | Cache QuerySpec, dùng model nhỏ cho rewriter |
| Cost tăng | Chỉ gọi LLM khi cần (QuerySpec fail, retry) |
| LLM output không ổn định | Structured output (JSON), validation, fallback rule |
| Regression | A/B test, giữ rule fallback tạm thời |

---

## 7. Thứ tự triển khai gợi ý

1. **Phase 1** – Ít thay đổi, tăng chất lượng QuerySpec.
2. **Phase 2** – Thêm QueryRewriter, thay fallback trong retrieval.
3. **Phase 3** – LLM retry strategy (có thể gộp vào Evidence Evaluator).
4. **Phase 4** – Dọn rule sau khi Phase 2–3 ổn định.

Phase 5 có thể làm song song từ Phase 2 (cache, A/B test).
