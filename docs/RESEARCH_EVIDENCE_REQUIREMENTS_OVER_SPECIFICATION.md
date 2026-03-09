# Hướng nghiên cứu: Xử lý over-specification và thiếu generic của refund/policy logic

## 1. Vấn đề

### 1.1. Case điển hình: "do greencloud accept refund for promo plan"

- **Evidence thực tế:** FAQ nói rõ "Special discounted plans are excluded from our refund policy."
- **Kết quả:** ASK_USER, không trả lời được dù evidence đã trả lời được câu hỏi (promo ≈ discounted → excluded).

**Nguyên nhân:**
1. Normalizer tạo `required_evidence: ["refund policy", "promo plan details"]` — trong đó `promo plan details` không phải evidence type chuẩn và KB không có mục riêng.
2. Evidence quality gate yêu cầu coverage cho **tất cả** hard_requirements; `promo plan details` = false → gate fail.
3. Phase 3 đã bỏ gray-zone override nên không còn đường trả lời bounded khi thiếu một requirement.

### 1.2. Logic refund/policy quá thiếu generic (vi phạm project-development)

Nhiều chỗ dùng **hardcoded regex/keywords** cho refund/policy, không config-driven, không scale:

| File | Vấn đề |
|------|--------|
| `branding_config.py` | Intent `refund_policy` với regex hẹp: chỉ match "do you have refund policy", "refund policy?", "chính sách hoàn tiền" — **bypass RAG** cho các query này. Không match "check refund", "refund for promo", "accept refund for X". |
| `reviewer.py` | `HIGH_RISK_PATTERNS = [r"\b(refund\|reimburse\|money back)\b", ...]` — hardcoded |
| `claim_parser.py` | `policy_phrases = [..., r"refund\|reimburse\|money back"]` — hardcoded |
| `evidence_set_builder.py` | `any(kw in text for kw in ["policy", "terms", "refund", "eligible"])` — hardcoded keywords |
| `opensearch_client.py` | Synonym `"refund, return, money back"` — có thể config được nhưng nằm trong code |

**Hậu quả:**
- Query "refund policy?" → intent cache → canned answer, không qua RAG.
- Query "refund for promo plan" → qua RAG nhưng bị over-specification chặn.
- Logic không scale: domain khác (e-commerce, SaaS, healthcare) cần từ khóa khác.

---

## 2. Mục tiêu nghiên cứu

- Giảm over-specification của required_evidence từ normalizer.
- Cho phép evidence quality gate chấp nhận evidence đủ để trả lời, kể cả khi không có coverage cho mọi requirement.
- **Làm generic logic refund/policy:** loại bỏ hardcoded regex/keywords, chuyển sang config-driven hoặc LLM-led.
- Không vi phạm project-development: không hardcode rules, giữ LLM làm orchestrator.

---

## 3. Hướng nghiên cứu

### 3.1. Normalizer: Constrain evidence types

**Ý tưởng:** Hướng dẫn normalizer chỉ dùng một tập evidence types chuẩn, tránh tạo requirement query-specific không có trong KB.

**Các bước:**
1. Liệt kê evidence types chuẩn từ evidence_selector và evidence_quality (policy_language, numbers_units, transaction_link, steps_structure, has_any_url, refund policy, …).
2. Cập nhật NORMALIZER_SYSTEM_PROMPT:
   - Thêm mục "Evidence types (use only these): policy_language, numbers_units, transaction_link, steps_structure, has_any_url, refund policy."
   - Hướng dẫn: "Do not invent granular types like 'promo plan details' or 'product X definition'. Use standard types that exist in the knowledge base."
   - Ví dụ: Với "refund for promo plan" → required_evidence: ["policy_language"] hoặc ["refund policy"], không thêm "promo plan details".
3. Đánh giá trên eval set: so sánh pass rate trước/sau, kiểm tra không regress các case khác.

**Rủi ro:** LLM có thể bỏ sót requirement thực sự cần thiết nếu constraint quá chặt.

---

### 3.2. Evidence quality: Semantic equivalence trong coverage

**Ý tưởng:** Khi evidence đã trả lời được câu hỏi (qua policy text), cho phép coverage "implied" cho requirement tương đương (promo ≈ discounted).

**Các bước:**
1. Cập nhật EVIDENCE_QUALITY_PROMPT:
   - Thêm: "When evidence explicitly states an exclusion or rule (e.g. 'special discounted plans excluded'), treat user terms (promo, discount, special offer) as synonymous for coverage. If the policy answers the question, set is_sufficient=true and coverage accordingly."
   - Thêm: "For policy/refund queries: policy_language or refund policy covering the rule is sufficient. Do not require a separate 'X plan details' if the policy text answers the question."
2. Thử nghiệm A/B: so sánh pass rate và hallucination rate trước/sau.
3. Kiểm tra edge case: không set is_sufficient=true khi evidence thực sự không trả lời được.

**Rủi ro:** Có thể quá lỏng, chấp nhận trả lời khi evidence chưa đủ.

---

### 3.3. Hard requirements: Relax khi evidence đã trả lời được

**Ý tưởng:** Khi LLM trả `gate_pass=true` nhưng một hard requirement chưa có coverage, vẫn cho pass nếu reason cho thấy evidence đã trả lời được.

**Các bước:**
1. Mở rộng logic trong `passes_quality_gate`:
   - Nếu `gate_pass=true` và `reason` chứa cụm kiểu "evidence states", "policy states", "explicitly states" → có thể relax `hard_ok` khi chỉ thiếu 1 requirement và requirement đó là "implied" bởi requirement khác (vd: promo plan details implied bởi policy_language).
2. Cần định nghĩa rõ "implied" — có thể dùng LLM nhỏ để classify, hoặc whitelist mapping (policy_language → implied: promo_plan_details khi query chứa "promo").
3. **Cảnh báo:** Cách này dễ vi phạm "no narrow rules". Chỉ nên dùng nếu 3.1 và 3.2 không đủ.

---

### 3.4. Bounded answer path (PASS_WEAK) khi partial coverage

**Ý tưởng:** Khi có policy_language/refund policy nhưng thiếu "promo plan details", cho phép PASS_WEAK với câu trả lời bounded thay vì ASK_USER.

**Các bước:**
1. Xem xét lại Phase 3: có nên cho phép một đường PASS_WEAK khi:
   - Có ít nhất 1 hard requirement được cover,
   - Evidence đủ để đưa ra câu trả lời bounded (vd: "Special discounted plans are excluded. Promo plans are typically considered discounted, so they are likely excluded. See Terms of Service for full details."),
   - Risk không phải high.
2. Cần sửa decision_router và/hoặc evidence_quality để có path này.
3. Đánh giá: tăng pass rate nhưng cần đảm bảo không tăng hallucination.

**Rủi ro:** Có thể quay lại vấn đề Phase 3 đã fix (quá nhiều override).

---

### 3.5. Làm generic logic refund/policy (config-driven, không hardcode)

**Ý tưởng:** Đưa mọi pattern refund/policy vào config hoặc DB, không hardcode trong code.

**Các bước:**

1. **Intent cache (branding_config):**
   - Bỏ intent `refund_policy` hardcoded — hoặc chuyển hoàn toàn sang DB (bảng `intents`).
   - Fallback intents không nên có regex domain-specific. Nếu cần shortcut cho "refund policy?" → dùng generic: "I'll search our docs for that. What specific aspect do you need?" (vẫn có thể gọi RAG) hoặc bỏ hẳn, để mọi query qua RAG.

2. **Reviewer HIGH_RISK_PATTERNS:**
   - Chuyển sang config: `reviewer_high_risk_patterns: str` (regex list, comma-separated hoặc JSON).
   - Default từ `.env` hoặc DB. Domain khác có thể override (e.g. healthcare: "liability", "malpractice").

3. **Claim parser policy_phrases:**
   - Chuyển sang config: `claim_parser_policy_phrases: list[str]` hoặc dùng LLM để detect policy-like claims (phù hợp project-development: "LLM evaluates quality").

4. **Evidence set builder `_chunk_satisfies_requirement`:**
   - Đã có `coverage_map` từ Evidence Selector (LLM) — ưu tiên dùng khi có.
   - Khi không có coverage_map: thay vì `["policy", "terms", "refund", "eligible"]`, dùng config `evidence_requirement_keywords: dict[str, list[str]]` mapping requirement → keywords. Empty = không dùng heuristic, chỉ tin LLM.

5. **OpenSearch synonyms:**
   - Đã có thể override qua index settings. Đảm bảo `refund, return, money back` không hardcode trong Python — load từ config hoặc index template.

**Tiêu chí:** Một tenant mới (e.g. e-commerce, healthcare) có thể chạy mà không sửa code — chỉ config/env.

---

## 4. Kế hoạch thử nghiệm

| Thứ tự | Hướng | Thời gian ước tính | Phụ thuộc |
|--------|-------|--------------------|-----------|
| 1 | 3.1 Normalizer constrain | 1–2 ngày | Không |
| 2 | 3.2 Evidence quality semantic equivalence | 1–2 ngày | Không |
| 3 | 3.5 Làm generic refund/policy logic | 2–3 ngày | Không |
| 4 | Đánh giá kết hợp 3.1 + 3.2 + 3.5 | 0.5 ngày | 1, 2, 3 |
| 5 | 3.4 Bounded path (nếu cần) | 2–3 ngày | 4 |
| 6 | 3.3 Relax hard_ok (chỉ khi cần) | 1 ngày | 5 |

---

## 5. Eval cases cần thêm

Thêm vào `offline_eval_replay_cases.jsonl`:

```jsonl
{"name":"promo_refund_policy_case","input":"does GreenCloud accept refund for promo plan?","tags":["policy_question","promo_refund","replay"],"expected_decision":"PASS","expected_chunk_ids":[],"required_evidence":["policy_language"],"expected_answer_contains":["excluded","discounted","special"],"forbidden_answer_contains":["guaranteed refund"]}
```

Và các biến thể: "refund for discount plan", "refund for special offer", "refund for promotional VPS".

---

## 6. Tiêu chí thành công

- **Pass rate:** Case "promo plan refund" và các biến thể tương tự pass (PASS hoặc PASS_WEAK với câu trả lời đúng).
- **Không regress:** Các case policy_question, pricing_question hiện tại vẫn pass.
- **Hallucination:** Không tăng tỷ lệ trả lời sai hoặc invent facts.
- **Tuân thủ project-development:** Không thêm keyword rules; mọi thay đổi qua prompt/config, không hardcode.
- **Generic:** Tenant mới (e-commerce, SaaS, healthcare) có thể chạy chỉ với config — không sửa code. Không còn hardcoded "refund", "reimburse", "money back" trong reviewer/claim_parser/evidence_set_builder.

---

## 7. Tài liệu tham khảo

- `docs/Assumption.md` — Target architecture, evidence flow
- `.cursor/rules/project-development.mdc` — No hardcoded rules, LLM-led
- `app/services/normalizer.py` — NORMALIZER_SYSTEM_PROMPT, _derive_hard_requirements
- `app/services/evidence_quality.py` — EVIDENCE_QUALITY_PROMPT, passes_quality_gate
