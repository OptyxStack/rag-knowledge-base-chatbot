# Phương án: LLM Orchestration + Review + English-only với Auto-translate

## 1. Tổng quan mục tiêu

- **LLM quyết định flow**: LLM orchestrator điều khiển các bước, quyết định bước tiếp theo
- **LLM review**: Sau mỗi bước quan trọng, LLM review output trước khi chuyển sang bước sau
- **English-only**: Chỉ xử lý tiếng Anh; input không phải English → LLM dịch sang English trước

---

## 2. Luồng đề xuất (high-level)

```
[Input] → Language Gate (detect + translate) → [English Query]
    ↓
[LLM Orchestrator] → Decide: Intent Cache? / Normalize? / Skip? / Ambiguous?
    ↓
[Normalizer] → QuerySpec
    ↓
[Review 1: QuerySpec] ← LLM validate intent, required_evidence
    ↓
[Retrieval] → EvidencePack
    ↓
[Review 2: Evidence] ← LLM validate relevance
    ↓
[Quality Gate] → pass/fail
    ↓
[Hybrid Decision Router] → PASS / ASK_USER / ESCALATE (Deterministic + LLM gray zone)
    ↓
[LLM Generate] → Answer + citations
    ↓
[Review 3: Answer] ← LLM validate grounded, citations
    ↓
[Output] (English)
```

---

## 3. Các bước nên có LLM Review sau khi hoàn thành

| # | Bước | Output cần review | Lý do review | Hành động khi fail |
|---|------|-------------------|--------------|--------------------|
| 1 | **Normalizer** | QuerySpec | Intent/entities/required_evidence sai → retrieval + answer sai | LLM suggest correction → retry normalizer hoặc override |
| 2 | **Retrieval** | EvidencePack | Evidence không liên quan → lãng phí token, answer kém | LLM suggest query rewrite → retry retrieval |
| 3 | **Answer Generation** | Answer + citations | Answer không grounded, citation sai | LLM suggest fix hoặc ESCALATE |

**Không cần LLM review riêng:**
- Language Gate: rule-based detect + gộp translate trong Normalizer
- Quality Gate: rule-based, explainable, ổn định

**Hybrid Decision Router:** Deterministic + LLM cho gray zone (xem Phase 4).

---

## 4. Chi tiết từng phase

### Phase 0: Language Gate (mới)

**Input:** `user_message`

**Logic:**
1. **Detect language**: Rule-based (langdetect, fasttext) hoặc LLM nhẹ
   - Nếu English → pass through
   - Nếu không English → gọi LLM translate sang English
2. **Output:** `(query_en: str, source_lang: str | None)`
   - `source_lang=None` → đã là English
   - `source_lang="vi"` → đã dịch từ tiếng Việt (có thể dùng để dịch answer về sau)

**Config:** `language_gate_enabled`, `language_gate_translate_non_english`

---

### Phase 1: LLM Orchestrator (mới)

**Input:** `query_en`, `conversation_history`

**LLM quyết định:**
- `skip_intent_cache`: có match intent cache không? (who am i, what can you do)
- `skip_retrieval`: có phải greeting không?
- `is_ambiguous`: query có mơ hồ không?
- `next_step`: "intent_cache" | "normalize" | "ask_user" | "retrieve"

**Output:** `OrchestratorDecision`

**Lợi ích:** Thay rule cứng bằng LLM, linh hoạt hơn với câu phức tạp.

---

### Phase 2: Normalizer (đã có)

**Output:** QuerySpec

**Review 1 – QuerySpec Review (mới):**
- LLM nhận: `query`, `QuerySpec`
- Hỏi: "Is intent correct? Are required_evidence appropriate for this query?"
- Output: `{ "pass": true | false, "suggestions": [...], "override": {...} }`
- Nếu pass=false: có thể override QuerySpec hoặc retry

---

### Phase 3: Retrieval (đã có)

**Output:** EvidencePack

**Review 2 – Evidence Relevance Review (mới):**
- LLM nhận: `query`, `evidence_summaries` (top 3–5 chunks)
- Hỏi: "Is this evidence relevant to the query? Should we retry with different query?"
- Output: `{ "pass": true | false, "suggested_query": "...", "reason": "..." }`
- Nếu pass=false: retry retrieval với suggested_query (nếu còn attempt)

---

### Phase 4: Quality Gate + Hybrid Decision Router

**Quality Gate:** Giữ rule-based.

**Hybrid Decision Router (mới):**
- Deterministic trước: high-risk + no policy → ESCALATE (bắt buộc)
- Gray zone: LLM quyết định PASS / ASK_USER / ESCALATE
- Ràng buộc: LLM không được override ESCALATE → PASS

---

### Phase 5: Answer Generation (đã có)

**Output:** Answer + citations

**Review 3 – Answer Review (nâng cấp Reviewer):**
- Hiện tại: Reviewer rule-based
- Đề xuất: **LLM Reviewer** thay cho hoặc bổ sung rule-based
- LLM nhận: `query`, `answer`, `citations`, `evidence`
- Hỏi: "Is the answer grounded in evidence? Are citations correct? Any hallucination?"
- Output: `{ "pass": true | false, "issues": [...], "suggested_fix": "..." }`
- Nếu pass=false: có thể retry generate với suggested_fix hoặc ASK_USER/ESCALATE

---

## 5. Kế hoạch phát triển (phases)

### Phase A: Language Gate (1–2 ngày)

| Task | Mô tả |
|------|-------|
| A1 | Thêm `langdetect` hoặc fasttext cho language detection |
| A2 | Thêm LLM translate step khi `lang != "en"` |
| A3 | Config: `language_gate_enabled`, `language_gate_translate_non_english` |
| A4 | Tích hợp vào `answer_service.generate()` đầu pipeline |

**Output:** Input luôn là English trước khi xử lý.

---

### Phase B: LLM Orchestrator (2–3 ngày)

| Task | Mô tả |
|------|-------|
| B1 | Thiết kế prompt `OrchestratorDecision` schema |
| B2 | Implement `orchestrator_llm.py` – gọi LLM, parse JSON |
| B3 | Thay thế logic: Intent Cache, skip_retrieval, ambiguous bằng Orchestrator output |
| B4 | Fallback: khi LLM fail → dùng rule-based hiện tại |
| B5 | Config: `orchestrator_use_llm` |

**Output:** LLM quyết định flow thay cho rule cố định.

---

### Phase C: QuerySpec Review (1–2 ngày)

| Task | Mô tả |
|------|-------|
| C1 | Prompt: "Review QuerySpec for query X. Is intent/required_evidence correct?" |
| C2 | Implement `review_query_spec()` – gọi LLM, parse pass/suggestions |
| C3 | Khi pass=false: override QuerySpec hoặc retry normalizer |
| C4 | Config: `normalizer_review_enabled` |

**Output:** QuerySpec được LLM review trước retrieval.

---

### Phase D: Evidence Relevance Review (1–2 ngày)

| Task | Mô tả |
|------|-------|
| D1 | Prompt: "Review evidence relevance for query X. Is it relevant?" |
| D2 | Implement `review_evidence_relevance()` |
| D3 | Khi pass=false: retry retrieval với suggested_query (attempt 2) |
| D4 | Config: `retrieval_review_enabled` |

**Output:** Evidence được LLM review trước khi generate.

---

### Phase E: LLM Answer Reviewer (2–3 ngày)

| Task | Mô tả |
|------|-------|
| E1 | Thiết kế prompt cho Answer Review (grounded, citations, hallucination) |
| E2 | Implement `LLMReviewerGate` – thay/bổ sung `ReviewerGate` |
| E3 | Khi pass=false: retry generate với feedback hoặc ASK_USER/ESCALATE |
| E4 | Config: `reviewer_use_llm` (rule-based vẫn làm fallback) |

**Output:** Answer được LLM review trước khi trả về user.

---

### Phase F: Tích hợp & tối ưu (1–2 ngày)

| Task | Mô tả |
|------|-------|
| F1 | Tổng hợp config, bật/tắt từng review |
| F2 | Điều chỉnh cost: dùng model nhẹ cho review (gpt-4o-mini) |
| F3 | Timeout, retry cho từng LLM call |
| F4 | Metrics: review_pass_rate, review_retry_count |

---

## 6. Thứ tự triển khai đề xuất

```
1. Phase A (Language Gate)     → Cơ sở: input luôn English
2. Phase C (QuerySpec Review) → Cải thiện chất lượng QuerySpec
3. Phase E (LLM Answer Review) → Cải thiện chất lượng answer (impact cao)
4. Phase D (Evidence Review)   → Giảm lãng phí token
5. Phase B (Orchestrator)     → Linh hoạt hóa flow (phức tạp nhất)
6. Phase F (Tích hợp)          → Hoàn thiện
```

---

## 7. Config tổng hợp

```env
# Language Gate
LANGUAGE_GATE_ENABLED=true
LANGUAGE_GATE_TRANSLATE_NON_ENGLISH=true

# Orchestrator
ORCHESTRATOR_USE_LLM=false

# Normalizer
NORMALIZER_USE_LLM=true
NORMALIZER_REVIEW_ENABLED=true

# Retrieval
RETRIEVAL_REVIEW_ENABLED=true

# Reviewer
REVIEWER_USE_LLM=true
REVIEWER_LLM_MODEL=gpt-4o-mini
```

---

## 8. Cost & Latency ước tính

| Bước | LLM call | Model | Token ước tính | Latency |
|------|----------|-------|----------------|---------|
| Language translate | 1 | gpt-4o-mini | ~500 | ~200ms |
| Orchestrator | 1 | gpt-4o-mini | ~300 | ~150ms |
| Normalizer | 1 | gpt-4o-mini | ~400 | ~200ms |
| QuerySpec Review | 1 | gpt-4o-mini | ~300 | ~150ms |
| Evidence Review | 1 | gpt-4o-mini | ~500 | ~200ms |
| Answer Generate | 1 | gpt-4o | ~2000 | ~1s |
| Answer Review | 1 | gpt-4o-mini | ~600 | ~250ms |

**Tổng thêm:** ~4–5 LLM calls (khi bật hết) → ~2–3s latency, ~$0.01–0.02/request (ước tính).

---

## 9. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|--------|------------|
| Cost tăng | Bật từng review qua config; dùng model nhẹ cho review |
| Latency cao | Chạy song song khi có thể; cache khi có thể |
| LLM review sai | Giữ rule-based làm fallback; log để phân tích |
| Dịch sai | Chỉ dịch khi detect chắc; có thể thêm "confidence" |

---

## 10. Tóm tắt

- **Language Gate**: Detect + translate non-English → English
- **Review points**: QuerySpec, Evidence, Answer (3 điểm)
- **Orchestrator**: LLM quyết định flow (optional, phase sau)
- **Thứ tự triển khai**: A → C → E → D → B → F
