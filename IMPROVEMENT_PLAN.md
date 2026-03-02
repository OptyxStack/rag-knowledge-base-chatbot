# Phương án cải thiện – Tiến tới Enterprise RAG (archi.md)

Tài liệu mô tả lộ trình triển khai các cải tiến để hệ thống tiệm cận kiến trúc trong `archi.md`.

---

## 1. Tổng quan lộ trình

```
Phase 0.5 (Evidence Hygiene) → Phase 1 (Quick wins)  → Phase 2 (Evidence-first)  → Phase 3 (Full flow)
────────────────────────────────────────────────────────────────────────────────────────────────────
Boilerplate + density log    → Evidence Quality Gate → Request Normalizer        → Decision Router
(measure before tune)        → Retry Planner         → User context              → Budget controls
                             → Observability         →                          → Tone compliance
```

**Nguyên tắc triển khai:**
- Mỗi phase có thể deploy độc lập, không breaking change
- Ưu tiên evidence-first: không generate khi evidence yếu
- Giữ backward compatibility với API hiện tại
- **Domain-agnostic:** Evidence scoring by features, not hardcoded domain logic; `doc_type` only as weak prior

---

## 2. Phase 0.5 – Evidence Hygiene (1 tuần)

**Mục tiêu:** Measure evidence characteristics *before* tuning gates. Log boilerplate and content density to inform threshold decisions.

**File:** `app/services/evidence_hygiene.py`

**Chức năng (logging only, no gating):**
- **Boilerplate detection:** Ratio of nav/footer signatures (contact, copyright, menu, terms, privacy) vs substantive content; log per-chunk and aggregate
- **Content density:** Non-whitespace chars, sentence count, list/structure presence; log distribution
- **Top evidence signatures** (aggregate, dashboard-friendly):
  - `pct_chunks_with_url`: % chunks có URL
  - `pct_chunks_with_number_unit`: % chunks có number+unit
  - `pct_chunks_boilerplate_gt_06`: % chunks boilerplate > 0.6
  - `median_content_density`: median content_density
  - → Nhìn dashboard biết ngay retrieval đang kéo "rác" hay "thịt"
- Output: structured logs (or debug metadata) for analysis

**Tích hợp:** Gọi sau `retrieve()`, log vào debug. Không block flow. Dùng data để tune Phase 1 thresholds.

**Deliverables:**
- [ ] Boilerplate ratio per chunk + aggregate
- [ ] Content density metrics per chunk
- [ ] Top evidence signatures: pct_with_url, pct_with_number_unit, pct_boilerplate_gt_06, median_content_density
- [ ] Log pipeline for dashboard/analysis

---

## 3. Phase 1 – Quick wins (2–3 tuần)

**Mục tiêu:** Thêm Evidence Quality Gate và Retry Planner cơ bản, cải thiện observability. Không thay đổi luồng chính.

### 3.1 Evidence Quality Gate (domain-agnostic)

**File:** `app/services/evidence_quality.py`

**Nguyên tắc:** Score by **evidence features**, not domain logic. `doc_type` chỉ dùng làm weak prior (hint), không hardcode policy/pricing.

**Input:** `EvidencePack`, `required_evidence` (list[str]) — optional; nếu không có thì score tất cả features

**Evidence features (per-chunk → aggregate):**

| Feature | Mô tả | Cách đo |
|---------|-------|---------|
| `numbers_units` | Số + đơn vị/tiền tệ | Pattern: `$`, `USD`, `/mo`, `\d+`, `%`, currency symbols |
| `has_any_url` | Có URL bất kỳ (docs link) | Regex URL hợp lệ |
| `has_transaction_link` | URL giao dịch (order/checkout/store) | Paths chứa order/store/checkout/cart → tránh "link rác" |
| `policy_language` | Ngôn ngữ chính sách (normative patterns) | Xem bảng dưới |
| `steps_structure` | Nội dung có cấu trúc thủ tục | Numbered list (1., 2.), "Step N", bullet sequences |
| `content_density` | Mật độ nội dung thực | Non-whitespace ratio, sentence count; tránh chunk rỗng |
| `boilerplate_ratio` | Tỷ lệ nav/footer | Keywords: contact, copyright, menu, terms → ratio thấp = tốt |
| `freshness` | Độ mới (nếu có metadata) | `effective_date` decay; không có → neutral |
| `trust_tier` | Độ tin cậy nguồn | `doc_type` weak prior: official > user-generated; weight nhỏ |

**policy_language — normative pattern groups (giảm false positive):**
- **obligation:** must, shall, required, prohibited
- **entitlement:** eligible, refund, within, fee applies
- **scope:** terms, policy, SLA, abuse, cancellation
→ Score dựa trên kết hợp nhiều nhóm; không chỉ keyword list đơn giản. Match 2+ nhóm → confidence cao hơn.

**QualityReport (explainable):**

```python
@dataclass
class QualityReport:
    quality_score: float              # 0–1, aggregate (optional check)
    feature_scores: dict[str, float]   # numbers_units, has_any_url, has_transaction_link, policy_language, ...
    missing_signals: list[str]         # derived: ["missing_numbers", "missing_transaction_link", ...]
    staleness_risk: float | None
    boilerplate_risk: float | None
```

- `missing_signals` **derived from feature_scores**: e.g. `numbers_units < 0.3` → `missing_numbers`; `has_transaction_link < 0.2` → `missing_transaction_link`
- Khi có `required_evidence`: map `numbers`→`numbers_units`, `links`→`has_any_url`, `transaction_link`→`has_transaction_link`, `policy_clause`→`policy_language`, `steps`→`steps_structure`
- **Query transactional** (order, buy, checkout) → yêu cầu `has_transaction_link`, không chỉ `has_any_url`

**Quyết định PASS (tránh aggregate che lấp thiếu feature):**

| Điều kiện | Kết quả |
|-----------|---------|
| **Bắt buộc** | `all(required_feature >= per_feature_threshold)` |
| Optional | `aggregate quality_score >= threshold` |

Tránh tình huống: density tốt + trust tốt → aggregate OK, nhưng thiếu numbers → vẫn PASS → LLM lại phải ASK_USER. **PASS chỉ khi tất cả required features đạt ngưỡng.**

**Config:**
```python
evidence_quality_threshold: float = 0.6       # aggregate (optional check)
evidence_quality_enabled: bool = True
evidence_feature_thresholds: dict[str, float] = {  # per-feature min, bắt buộc khi required
    "numbers_units": 0.3,
    "has_transaction_link": 0.2,
    "policy_language": 0.3,
    ...
}
```

**Tích hợp:** Gọi trong `AnswerService.generate()` sau `retrieve()`, trước khi build messages cho LLM. Nếu **bất kỳ required feature** không đạt → chuyển sang Retry Planner.

---

### 3.2 Retry Planner (fixed ladder, max 2 attempts)

**File:** `app/services/retry_planner.py`

**Fixed retry ladder:**
- **Max 2 attempts** (Attempt 1 + Attempt 2)
- **Attempt 1:** Broad hybrid — BM25 + Vector + Fusion + Rerank (không thay đổi)
- **Attempt 2:** Precision targeted by `missing_signals` (+ optional context expansion)

**Chức năng:**
- Input: `QualityReport.missing_signals[]`, `attempt` (1 or 2)
- Output: `RetryStrategy` (boost_patterns, filter_doc_types, exclude_patterns, context_expansion)

**Mapping (Attempt 2 only):**

| missing_signal | Retry strategy |
|----------------|----------------|
| `missing_numbers` | Thêm patterns `$ USD /mo monthly \d+` vào keyword query; phrase boost |
| `missing_links` / `missing_transaction_link` | Boost fields chứa URL; context expansion (parent + neighbors) |
| `missing_policy` | Restrict `doc_type` to `{policy, tos}` (weak prior); boost policy language patterns |
| `boilerplate_risk` | Filter by content density; exclude nav/footer; **bật context_expansion = parent+neighbors** cho top chunks có dấu hiệu "menu page" |
| `staleness_risk` | Boost `effective_date` gần đây (nếu index có field) |

**Context expansion — default tool khi boilerplate cao:**
- Trong case pricing/nav: Attempt 2 không chỉ boost regex
- **Bật luôn** `context_expansion = parent + neighbors` cho các chunk top có dấu hiệu "menu page" (boilerplate cao, ít nội dung thực)
- Lấy thêm parent doc / neighbor chunks để tìm "thịt" bên trong trang

**LLM suggested_queries:** Chỉ dùng làm **fallback** khi không có QualityReport hoặc khi Retry Planner không map được missing_signals. Không dùng làm primary retry trigger.

**Tích hợp:**
- `RetrievalService.retrieve()` nhận `retry_strategy: RetryStrategy | None`, `attempt: int`
- Attempt 1: `retry_strategy=None` → broad hybrid
- Attempt 2: `retry_strategy` từ Retry Planner → precision targeted

---

### 3.3 Observability

**Cập nhật:**
- Log `QualityReport` (bao gồm `feature_scores`, `missing_signals`) vào debug metadata
- Log `RetryStrategy` khi retry
- Log cost (từ `estimate_cost`) vào debug mỗi request
- Thêm metric `evidence_quality_score` (histogram), `evidence_feature_scores` (optional)

---

### 3.4 Luồng Phase 1 (sau khi hoàn thành)

```
Input → Intent Cache → Retrieval
  → Evidence Quality Gate
    → all required features >= threshold: tiếp tục
    → any required feature < threshold: Retry Planner → Retrieval (retry)
  → [Generate Answer] → Reviewer Gate → [Retry nếu RETRIEVE_MORE] → Output
```

**Lưu ý:** `required_evidence` ban đầu có thể infer từ query (rule-based), ví dụ:
- Query có "price", "cost" → `required_evidence = ["numbers", "transaction_link"]` (transactional)
- Query có "link", "order" → `required_evidence = ["transaction_link"]`
- Query có "refund", "policy" → `required_evidence = ["policy_clause"]`

---

## 4. Phase 2 – Request Normalization (3–4 tuần)

**Mục tiêu:** Thêm Normalizer tạo QuerySpec, chuẩn hóa input trước retrieval.

### 4.1 QuerySpec schema

**File:** `app/services/schemas.py` (hoặc `app/api/schemas.py` mở rộng)

```python
@dataclass
class QuerySpec:
    intent: str  # informational | transactional | policy | troubleshooting | account | ...
    entities: list[str]  # domain objects extracted
    constraints: dict[str, Any]  # budget < 10, region=SG, etc.
    required_evidence: list[str]  # ["numbers", "links", "transaction_link", "policy_clause", "steps", "citations"]
    risk_level: str  # low | medium | high
    keyword_queries: list[str]
    semantic_queries: list[str]
    clarifying_questions: list[str]  # optional, do not ask yet
```

### 4.2 Normalizer

**File:** `app/services/normalizer.py`

**Chọn 1 trong 2:**

**Option A – Rule-based (nhanh, ít cost):**
- Pattern match cho intent (giống intent_cache mở rộng)
- Keyword extraction cho entities
- Heuristic cho required_evidence từ intent
- Risk level từ keyword (refund, legal, billing → high)

**Option B – LLM-small (chính xác hơn):**
- Gọi LLM nhỏ (gpt-4o-mini, Claude Haiku) với prompt structured
- Output JSON → QuerySpec
- Cần prompt engineering + fallback khi parse fail

**Khuyến nghị:** Bắt đầu Option A, sau đó thêm Option B làm enhancement (config switch).

### 4.3 Tích hợp Normalizer

- `AnswerService.generate()`: gọi Normalizer trước Retrieval
- Nếu có QuerySpec: truyền `keyword_queries`, `semantic_queries`, `required_evidence` vào Retrieval và Evidence Quality Gate
- Nếu không có (fallback): dùng luồng hiện tại (QueryRewrite)

### 4.4 User context (optional trong Phase 2)

**API schema:**
```python
class MessageCreate(BaseModel):
    content: str
    # Optional, for future multi-tenant
    tenant_id: str | None = None
    locale: str | None = None  # vi, en
```

- Lấy từ `conversation.metadata` hoặc header nếu có
- Truyền vào Normalizer để điều chỉnh (ví dụ locale → clarifying_questions ngôn ngữ phù hợp)

---

## 5. Phase 3 – Decision Router & Budget (2–3 tuần)

**Mục tiêu:** Decision Router chạy trước Answer Generation; thêm budget controls.

### 5.1 Decision Router (pre-answer)

**File:** `app/services/decision_router.py`

**Logic:**
- Input: `QualityReport`, `QuerySpec.risk_level`, `constraints` completeness
- Output: `PASS` | `ASK_USER` | `ESCALATE` + **reason** (để phân biệt loại ASK_USER)

**Phân biệt ASK_USER (pre-generation):**

| Nguyên nhân | Decision | Response type |
|-------------|----------|---------------|
| **Missing constraints** | ASK_USER | "I need a bit more info: budget? region? plan type?" — clarifying questions |
| **Missing evidence quality** | ASK_USER | "I couldn't find enough specific info on X. Could you rephrase or specify?" — evidence gap |
| all required features >= threshold AND (optional) aggregate >= threshold | PASS | → Generate |
| high-risk AND insufficient/ambiguous evidence | ESCALATE | → Human |

**Output schema:**
```python
@dataclass
class DecisionResult:
    decision: str  # PASS | ASK_USER | ESCALATE
    reason: str    # "missing_constraints" | "missing_evidence_quality" | "sufficient" | "high_risk_insufficient"
    clarifying_questions: list[str]  # for ASK_USER
    partial_links: list[str]         # for ASK_USER (evidence gap)
```

**ASK_USER response (missing_constraints):**
- State what constraints are missing
- Ask 1–3 clarifying questions
- Tone human

**ASK_USER response (missing_evidence_quality):**
- State what evidence is missing (from `missing_signals`)
- Provide partial useful links (nếu có)
- Suggest rephrase or narrow scope
- Tone human, không như system error

**Tích hợp:** Chạy sau Evidence Quality Gate, trước khi gọi LLM. Nếu ASK_USER hoặc ESCALATE → return ngay, không generate.

### 5.2 Budget controls

**Config:**
```python
retrieval_latency_budget_ms: int = 5000  # tổng thời gian retrieval qua các attempt
retrieval_token_budget: int = 0  # 0 = không giới hạn
```

**Logic:**
- Trong retry loop: đo tổng latency từ retrieval
- Nếu vượt budget → stop retry, escalate hoặc ASK_USER
- Token budget: nếu dùng LLM cho normalizer, cộng dồn token; nếu vượt → skip normalizer hoặc dùng rule-based

---

## 6. Phase 4 – Polish (1–2 tuần)

### 6.1 Answer QA Gate – Tone compliance

- Thêm check trong Reviewer: tone nên human, không robotic
- Heuristic: câu quá dài, quá nhiều bullet, không có câu hỏi ngược → flag

### 6.2 Full observability

- Log QuerySpec mỗi request
- Dashboard/metrics: quality_score distribution, retry rate by missing_signal, decision latency

---

## 7. Thứ tự triển khai đề xuất

| # | Task | Phase | Effort | Phụ thuộc |
|---|------|-------|--------|-----------|
| 0 | Evidence Hygiene (boilerplate + density logging) | 0.5 | 3–4 ngày | 0 |
| 1 | Evidence Quality Gate (domain-agnostic, feature scores) | 1 | 3–4 ngày | 0 |
| 2 | Retry Planner (fixed ladder, max 2, Attempt2 precision) | 1 | 2–3 ngày | 1 |
| 3 | Tích hợp Gate + Planner vào AnswerService | 1 | 1–2 ngày | 1, 2 |
| 4 | Observability (QualityReport feature_scores, metrics) | 1 | 1 ngày | 3 |
| 5 | QuerySpec schema + Rule-based Normalizer | 2 | 3–4 ngày | 0 |
| 6 | Tích hợp Normalizer vào flow | 2 | 2 ngày | 5, 3 |
| 7 | User context (optional) | 2 | 1 ngày | 6 |
| 8 | Decision Router (ASK_USER: constraints vs evidence) | 3 | 2–3 ngày | 1, 5 |
| 9 | Budget controls | 3 | 1–2 ngày | 8 |
| 10 | Tone compliance + Polish | 4 | 1–2 ngày | 0 |

---

## 8. Luồng cuối cùng (sau khi hoàn thành)

```
Input → Intent Cache → Normalizer → QuerySpec
  → Retrieval (Attempt 1: broad hybrid)
  → Evidence Quality Gate (feature scores → QualityReport)
    → any required feature < threshold: Retry Planner (missing_signals) → Retrieval (Attempt 2: precision + context expansion)
    → all required features OK: Decision Router
      → PASS: Answer Generator → Answer QA Gate → Output
      → ASK_USER (missing_constraints): clarifying questions (no LLM)
      → ASK_USER (missing_evidence_quality): evidence gap response (no LLM)
      → ESCALATE: Return escalation (no LLM)
```

---

## 9. Rủi ro & mitigations

| Rủi ro | Mitigation |
|--------|------------|
| Evidence Quality Gate quá strict → nhiều ASK_USER | Per-feature thresholds bắt đầu thấp (0.2–0.3); tune theo Phase 0.5 dashboard |
| Normalizer LLM tăng latency | Dùng Option A (rule-based) trước; LLM async nếu cần |
| Breaking change API | Giữ nguyên MessageCreate; user_context optional |
| Retry quá nhiều → chậm | Max 2 attempts; budget latency |

---

## 10. Checklist triển khai

### Phase 0.5 – Evidence Hygiene
- [ ] Tạo `app/services/evidence_hygiene.py`: boilerplate detection + content density
- [ ] Top evidence signatures: pct_with_url, pct_with_number_unit, pct_boilerplate_gt_06, median_content_density
- [ ] Log per-chunk và aggregate vào debug (không gating)
- [ ] Pipeline log cho analysis/dashboard

### Phase 1 – Evidence Quality Gate + Retry Planner
- [ ] Tạo `app/services/evidence_quality.py`: domain-agnostic scoring by features
- [ ] Split links: `has_any_url`, `has_transaction_link`; transactional query → require transaction_link
- [ ] `policy_language` dựa trên normative patterns (obligation, entitlement, scope)
- [ ] PASS = all required features >= per-feature thresholds (không chỉ aggregate)
- [ ] `QualityReport` với `feature_scores`, `missing_signals` (derived from features)
- [ ] Retry Attempt 2: context_expansion default khi boilerplate cao (menu page chunks)
- [ ] Tạo `app/services/retry_planner.py`: fixed ladder (max 2), Attempt2 precision by missing_signals
- [ ] LLM suggested_queries chỉ dùng fallback
- [ ] Cập nhật `RetrievalService.retrieve()` nhận `retry_strategy`, `attempt`
- [ ] Cập nhật `AnswerService.generate()`: Gate trước generate, Retry Planner cho Attempt 2
- [ ] Log QualityReport (feature_scores, missing_signals), RetryStrategy
- [ ] Thêm metric `evidence_quality_score`
- [ ] Unit test cho Evidence Quality Gate và Retry Planner

### Phase 2 – Request Normalization
- [x] QuerySpec schema (`app/services/schemas.py`)
- [x] Rule-based Normalizer (`app/services/normalizer.py`): intent, entities, required_evidence, risk_level
- [x] Ambiguity detection ("what diff from this?" + pasted content)
- [x] Tích hợp Normalizer vào AnswerService (trước Retrieval)
- [x] RetrievalService nhận `query_spec`, dùng keyword_queries/semantic_queries khi có

### Phase 3 – Decision Router
- [x] Decision Router (`app/services/decision_router.py`)
- [x] `DecisionResult.reason`: `ambiguous_query` | `missing_constraints` | `missing_evidence_quality` | `high_risk_insufficient` | `sufficient`
- [x] ASK_USER response khác nhau theo reason (no LLM call)
- [x] Tích hợp Decision Router vào AnswerService (sau Evidence Quality Gate, trước LLM)
- [x] Budget controls: `retrieval_latency_budget_ms`, `retrieval_token_budget`

---

*Cập nhật: Phase 2 Normalizer + Phase 3 Decision Router triển khai xong. Ambiguity short-circuit trước retrieval.*
