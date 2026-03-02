# So sánh archi_v3 vs PHUONG_AN_LLM_ORCHESTRATION – Ưu nhược & Điều chỉnh kế hoạch

## 1. Có nên upgrade sang archi_v3 không?

**Kết luận: Có, nên upgrade.** archi_v3 thiết kế rõ ràng hơn, tách bạch LLM vs Deterministic, và phù hợp production.

---

## 2. So sánh chi tiết

| Khía cạnh | archi_v3 | PHUONG_AN hiện tại |
|-----------|----------|---------------------|
| **Translation** | Gộp trong LLM Normalizer (1 step) | Language Gate riêng (detect + translate) |
| **Orchestrator** | Không có – flow cố định | LLM Orchestrator (optional) |
| **Normalizer** | LLM Normalizer: translate + QuerySpec trong 1 call | Normalizer + QuerySpec Review riêng |
| **Evidence** | LLM Evidence Evaluator → advise Retry Planner | Evidence Relevance Review → retry |
| **Answer** | Generate → Self-Critic → Citation Validator → Final Polish | Generate → Answer Review |
| **Translate-back** | Không (output luôn English) | Không |
| **Retry** | Self-critic: max 1 regenerate | Review fail → retry hoặc ESCALATE |

---

## 3. Ưu điểm archi_v3

| Ưu điểm | Chi tiết |
|---------|----------|
| **Gọn hơn** | Translation gộp trong Normalizer → ít step, ít LLM call |
| **Rõ ràng** | Phân tách: LLM (hiểu, suy luận) vs Deterministic (an toàn, audit) |
| **Self-Critic** | Generate → Self-Critic → regenerate 1 lần nếu fail → giới hạn retry |
| **Final Polish** | Cải thiện clarity, structure, tone (không sửa nội dung thực tế) |
| **Evidence Evaluator** | Chỉ advise, không override deterministic gate |
| **Auditability** | Deterministic gates giữ nguyên, dễ trace |

---

## 4. Nhược điểm / Rủi ro archi_v3

| Nhược điểm | Giảm thiểu |
|------------|------------|
| **LLM Normalizer nặng** | Dùng model nhẹ (gpt-4o-mini), tối ưu prompt |
| **Nhiều LLM call** | Bật/tắt từng bước qua config; cache khi có thể |
| **Final Polish có thể lệch** | Prompt rõ: "Cannot modify factual content" |
| **Self-critic + regenerate** | Giới hạn 1 lần regenerate, tránh loop |

---

## 5. Điều chỉnh kế hoạch PHUONG_AN để align archi_v3

### 5.1 Bỏ / Gộp

| Bỏ | Lý do |
|----|-------|
| **Language Gate riêng** | Gộp vào LLM Normalizer |
| **LLM Orchestrator** | archi_v3 dùng flow cố định, đơn giản hơn |
| **QuerySpec Review riêng** | Tin LLM Normalizer; nếu cần có thể thêm lightweight check |

### 5.2 Thêm / Thay đổi

| Thêm | Mô tả |
|------|-------|
| **LLM Normalizer language-aware** | Nhận `source_lang`, translate nội bộ nếu cần, output `canonical_query_en` + QuerySpec |
| **LLM Evidence Evaluator** | Thay Evidence Review; output `relevance_score`, `coverage_gaps`, `retry_needed`, `suggested_query` – chỉ advise Retry Planner |
| **LLM Self-Critic** | Sau Generate; check unsupported claims, incomplete; fail → regenerate 1 lần |
| **Deterministic Citation Validator** | Giữ rule-based (đã có trong Reviewer) |
| **LLM Final Polish** | Cải thiện clarity, structure, tone |
| **Hybrid Decision Router** | Deterministic rules trước; gray zone → LLM quyết định; LLM không override ESCALATE |

### 5.3 Bỏ

| Bỏ | Lý do |
|----|-------|
| **Translate-back** | Output cho client luôn là English |

### 5.4 Giữ nguyên

- Deterministic Evidence Quality Gate
- Retrieval (Attempt 1 + 2, Retry Planner)  
- Intent cache, skip_retrieval, ambiguous (có thể xử lý trong Normalizer)

### 5.5 Hybrid Decision Router (mới)

| Thành phần | Mô tả |
|------------|-------|
| **Deterministic trước** | High-risk + no policy → ESCALATE (bắt buộc); các rule cứng khác |
| **LLM khi gray zone** | Quality gate pass nhưng evidence yếu; quality fail nhưng có partial info; risk không rõ |
| **Ràng buộc** | LLM không được chuyển ESCALATE → PASS |

---

## 6. Kế hoạch phát triển điều chỉnh (theo archi_v3)

### Phase A: Language Detection + LLM Normalizer (2–3 ngày)

| Task | Mô tả |
|------|-------|
| A1 | Thêm `langdetect` (non-LLM) → `source_lang` |
| A2 | Nâng cấp LLM Normalizer: nhận `source_lang`, translate nội bộ nếu ≠ en |
| A3 | Output: `canonical_query_en`, QuerySpec, `intent_cache_match`, `is_ambiguous`, `query_rewrites` |
| A4 | Bỏ Language Gate riêng; tích hợp vào đầu pipeline |

**Output:** Một LLM call cho: detect + translate (nếu cần) + QuerySpec.

---

### Phase B: LLM Evidence Evaluator (1–2 ngày)

| Task | Mô tả |
|------|-------|
| B1 | Prompt: đánh giá relevance, coverage gaps, retry_needed, suggested_query |
| B2 | Output schema: `{ relevance_score, coverage_gaps, retry_needed, suggested_query }` |
| B3 | Chỉ advise: Retry Planner dùng `suggested_query` khi retry_needed |
| B4 | Deterministic Quality Gate vẫn quyết định pass/fail |

**Output:** LLM Evidence Evaluator bổ sung input cho Retry Planner.

---

### Phase C: LLM Self-Critic + Regenerate (2 ngày)

| Task | Mô tả |
|------|-------|
| C1 | Prompt: check unsupported claims, incomplete, hallucination |
| C2 | Output: `{ pass, issues, suggested_fix }` |
| C3 | Fail → regenerate 1 lần với feedback (max 2 generation attempts) |
| C4 | Deterministic Citation Validator sau Generate (giữ logic hiện tại) |

**Output:** Self-Critic + 1 lần regenerate nếu fail.

---

### Phase D: LLM Final Polish (1 ngày)

| Task | Mô tả |
|------|-------|
| D1 | Prompt: cải thiện clarity, structure, tone; không sửa factual content |
| D2 | Chạy sau Citation Validator |
| D3 | Config: `final_polish_enabled` |

**Output:** Answer được polish trước khi trả về.

---

### Phase E: Hybrid Decision Router (1–2 ngày)

| Task | Mô tả |
|------|-------|
| E1 | Chạy Deterministic rules trước; nếu clear (vd: ESCALATE high-risk) → dùng luôn |
| E2 | Gray zone: gọi LLM với query, QuerySpec, evidence summary, quality_report |
| E3 | Output: `{ decision, reason, confidence, clarifying_questions, partial_links }` |
| E4 | Ràng buộc: LLM không được override ESCALATE → PASS |
| E5 | Config: `decision_router_use_llm` |

**Output:** Decision Router hybrid – rule cứng + LLM cho gray zone.

---

### Phase F: Tích hợp & tối ưu (1–2 ngày)

| Task | Mô tả |
|------|-------|
| F1 | Refactor `answer_service` theo flow archi_v3 |
| F2 | Config tổng hợp, bật/tắt từng bước |
| F3 | Metrics, logging, observability |
| F4 | Fallback khi LLM fail |

---

## 7. Thứ tự triển khai đề xuất

```
1. Phase A (Language Detect + LLM Normalizer)  → Nền tảng
2. Phase B (Evidence Evaluator)                → Cải thiện retrieval
3. Phase C (Self-Critic + Regenerate)         → Cải thiện answer
4. Phase D (Final Polish)                     → UX
5. Phase E (Hybrid Decision Router)           → Quyết định gray zone
6. Phase F (Tích hợp)                         → Hoàn thiện
```

---

## 8. Flow tổng hợp (archi_v3)

```
Input
  ↓
detect_language (fast, non-LLM)
  ↓
LLM Normalizer (language-aware, translate nội bộ nếu cần)
  → intent_cache_match? → return
  → is_ambiguous? → ask_user
  ↓
Retrieval Attempt 1
  ↓
LLM Evidence Evaluator (advise)
  ↓
Deterministic Evidence Quality Gate
  ├── FAIL → Retry Planner → Retrieval Attempt 2
  └── PASS
        ↓
Hybrid Decision Router (Deterministic + LLM gray zone)
  ├── ASK_USER / ESCALATE → return
  └── PASS
        ↓
LLM Answer Generation
  ↓
LLM Self-Critic
  ├── FAIL → Regenerate (max 1)
  └── PASS
        ↓
Deterministic Citation Validator
  ↓
LLM Final Polish
  ↓
Output (English)
```

---

## 9. Config đề xuất (archi_v3)

```env
# Language
LANGUAGE_DETECT_ENABLED=true

# Normalizer (gộp translate + QuerySpec)
NORMALIZER_USE_LLM=true
NORMALIZER_LLM_MODEL=gpt-4o-mini

# Evidence Evaluator
EVIDENCE_EVALUATOR_ENABLED=true
EVIDENCE_EVALUATOR_LLM_MODEL=gpt-4o-mini

# Self-Critic
SELF_CRITIC_ENABLED=true
SELF_CRITIC_REGENERATE_MAX=1

# Final Polish
FINAL_POLISH_ENABLED=true

# Hybrid Decision Router
DECISION_ROUTER_USE_LLM=true
DECISION_ROUTER_LLM_MODEL=gpt-4o-mini
```

---

## 10. Tóm tắt

| Câu hỏi | Trả lời |
|---------|---------|
| **Có nên upgrade sang archi_v3?** | Có – thiết kế rõ, phù hợp production |
| **Ưu điểm chính** | Gọn (gộp translate), Self-Critic + regenerate có giới hạn, Final Polish, Hybrid Decision Router |
| **Điều chỉnh kế hoạch** | Bỏ Language Gate riêng + Orchestrator + Translate-back; gộp translate vào Normalizer; thêm Evidence Evaluator, Self-Critic, Final Polish, Hybrid Decision Router |
| **Thứ tự** | A → B → C → D → E → F |
