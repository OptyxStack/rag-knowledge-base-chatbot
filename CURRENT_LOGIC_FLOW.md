# Current Logic Flow – Auto-Reply Chatbot

Document describing the processing flow from receiving a question to returning an answer.

---

## 1. Main Flow Overview

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                           API: POST /conversations/{id}/messages                         │
│                                    (body: { content })                                    │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  Guardrails: check_injection() → sanitize_user_input()                                   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                         AnswerService.generate(query, conversation_history)              │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detailed Flow in AnswerService.generate()

```mermaid
flowchart TD
    START([User Message]) --> INTENT_CACHE{Intent Cache Hit?<br/>match_intent}
    INTENT_CACHE -->|Yes| RETURN_CACHE[Return cached answer<br/>PASS, no LLM]
    INTENT_CACHE -->|No| NORMALIZER[Normalizer: QuerySpec<br/>intent, entities, required_evidence, risk_level]
    
    NORMALIZER --> SKIP_RETRIEVAL{skip_retrieval?<br/>greeting/social}
    SKIP_RETRIEVAL -->|Yes| RETURN_CANNED[Return canned_response<br/>PASS, no LLM]
    SKIP_RETRIEVAL -->|No| AMBIGUOUS{is_ambiguous?}
    
    AMBIGUOUS -->|Yes| DR_AMBIG[Decision Router<br/>ASK_USER + clarifying_questions]
    DR_AMBIG --> RETURN_AMBIG[Return, no retrieval]
    
    AMBIGUOUS -->|No| RETRIEVAL_LOOP[Retrieval Loop<br/>attempt 1..max_attempts]
    
    RETRIEVAL_LOOP --> RETRY_STRATEGY{attempt == 2<br/>+ quality_report?}
    RETRY_STRATEGY -->|Yes| PLAN_RETRY[Retry Planner<br/>plan_retry missing_signals]
    RETRY_STRATEGY -->|No| RETRIEVE[RetrievalService.retrieve]
    PLAN_RETRY --> RETRIEVE
    
    RETRIEVE --> RETRIEVAL_LOOP_INNER[Retrieval Pipeline]
    
    subgraph RETRIEVAL_LOOP_INNER[Retrieval Pipeline]
        QR[Query Rewrite<br/>QuerySpec or conversation context]
        BM25[OpenSearch BM25]
        VECTOR[Qdrant Vector]
        MERGE[Merge RRF]
        RERANK[Reranker]
        EVIDENCE[EvidencePack]
        QR --> BM25
        QR --> VECTOR
        BM25 --> MERGE
        VECTOR --> MERGE
        MERGE --> RERANK
        RERANK --> EVIDENCE
    end
    
    EVIDENCE --> NO_EVIDENCE{evidence empty?}
    NO_EVIDENCE -->|Yes| RETURN_ASK_USER1[Return ASK_USER<br/>no evidence found]
    
    NO_EVIDENCE -->|No| HYGIENE[Evidence Hygiene<br/>compute_hygiene - log only]
    HYGIENE --> QUALITY[Evidence Quality Gate<br/>evaluate_quality + passes_quality_gate]
    
    QUALITY --> QUALITY_FAIL{passes?}
    QUALITY_FAIL -->|No| RETRY_LOOP{attempt < max?}
    RETRY_LOOP -->|Yes| RETRIEVAL_LOOP
    RETRY_LOOP -->|No| QUALITY_FAIL_CONTINUE[Continue to Decision Router]
    
    QUALITY_FAIL -->|Yes| DR[Decision Router]
    QUALITY_FAIL_CONTINUE --> DR
    
    DR --> DR_PASS{decision == PASS?}
    DR_PASS -->|No| RETURN_DR[Return ASK_USER/ESCALATE<br/>pre-generated answer, no LLM]
    
    DR_PASS -->|Yes| BUILD_MSG[Build messages<br/>system + history + evidence]
    BUILD_MSG --> LLM[LLM Gateway.chat]
    
    LLM --> PARSE[_parse_llm_response]
    PARSE --> REVIEWER[Reviewer Gate]
    
    REVIEWER --> REVIEW_STATUS{status?}
    REVIEW_STATUS -->|PASS| RETURN_PASS[Return PASS + answer + citations]
    REVIEW_STATUS -->|ASK_USER| RETURN_ASK_USER2[Return ASK_USER]
    REVIEW_STATUS -->|ESCALATE| RETURN_ESCALATE[Return ESCALATE]
    REVIEW_STATUS -->|RETRIEVE_MORE| RETRY_MORE{attempt < max?}
    RETRY_MORE -->|Yes| RETRIEVAL_LOOP
    RETRY_MORE -->|No| RETURN_MAX[Return ASK_USER<br/>max attempts reached]
```

---

## 3. Main Modules and Order

| Phase | Module | Description |
|-------|--------|-------------|
| 0 | Intent Cache | `match_intent()` – cache common questions (who am i, what can you do) |
| 0.5 | Evidence Hygiene | `compute_hygiene()` – log metrics, no gating |
| 1 | Normalizer | `normalize()` → **QuerySpec** (intent, entities, required_evidence, risk_level, is_ambiguous) |
| 2 | Retrieval | `RetrievalService.retrieve()` – BM25 + Vector + RRF + Rerank |
| 2b | Retry Planner | `plan_retry(missing_signals, 2)` – Attempt 2: boost_patterns, filter_doc_types |
| 3 | Evidence Quality Gate | `evaluate_quality()` + `passes_quality_gate()` – check required_evidence |
| 4 | Decision Router | `route()` – PASS / ASK_USER / ESCALATE (before LLM) |
| 5 | LLM | `LLMGateway.chat()` – generate answer |
| 6 | Reviewer Gate | `review()` – check citations, policy, confidence |

---

## 4. Decision Router – Logic

```mermaid
flowchart TD
    DR_IN[QuerySpec, QualityReport, evidence, passes_quality_gate] --> A1{is_ambiguous?}
    A1 -->|Yes| ASK_USER1[ASK_USER<br/>clarifying_questions]
    
    A1 -->|No| A2{risk_level == high<br/>AND !passes_quality_gate?}
    A2 -->|Yes| ESCALATE[ESCALATE]
    
    A2 -->|No| A3{passes_quality_gate?}
    A3 -->|No| ASK_USER2[ASK_USER<br/>missing_evidence_quality<br/>partial_links]
    
    A3 -->|Yes| A4{missing_constraints?}
    A4 -->|Yes| ASK_USER3[ASK_USER<br/>clarifying_questions]
    
    A4 -->|No| PASS[PASS → proceed to LLM]
```

---

## 5. Retry Planner – Logic (Attempt 2)

| missing_signal | boost_patterns | filter_doc_types | exclude_patterns |
|----------------|---------------|------------------|------------------|
| missing_numbers | $, USD, /mo, /month, pricing, \d+ | - | - |
| missing_links | https://, order, store | - | - |
| missing_transaction_link | order, checkout, store | - | - |
| missing_policy | policy, terms, refund | policy, tos | - |
| missing_steps | step, 1., 2., first | - | - |
| boilerplate_risk | - | - | menu, footer, copyright |
| staleness_risk | - | - | - |

---

## 6. Reviewer Gate – Logic

```mermaid
flowchart TD
    R_IN[decision, answer, citations, evidence, confidence] --> R1{decision == PASS?}
    R1 -->|No| R1_OUT[PASS through]
    
    R1 -->|Yes| R2{high_risk_query?}
    R2 -->|Yes| R2A{has_policy_citation?}
    R2A -->|No| ESCALATE[ESCALATE]
    R2A -->|Yes| R3A
    R2 -->|No| R3A{citation_coverage >= threshold?}
    
    R3A -->|No| R3B{uncited_numbers?}
    R3B -->|Yes| ASK_USER[ASK_USER]
    R3B -->|No| R3C{uncited_policy_claims?}
    R3C -->|Yes| ASK_USER
    R3C -->|No| RETRIEVE_MORE[RETRIEVE_MORE<br/>if attempt < max]
    
    R3A -->|Yes| PASS[PASS]
```

---

## 7. Retrieval Pipeline – Details

```mermaid
flowchart LR
    subgraph Input
        Q[query]
        HIST[conversation_history]
        RETRY[retry_strategy]
        QSPEC[query_spec]
    end
    
    subgraph QueryRewrite
        QW[QuerySpec.keyword_queries / semantic_queries<br/>or _rewrite_with_conversation<br/>+ boost_patterns from retry]
    end
    
    subgraph Search
        BM25[OpenSearch BM25<br/>+ intent-aware fetch pricing]
        VEC[Qdrant Vector]
    end
    
    subgraph Merge
        RRF[RRF fusion<br/>or simple dedupe]
    end
    
    subgraph PostProcess
        RERANK[Reranker]
        EXCLUDE[exclude_patterns filter]
        DIVERSITY[ensure_doc_type_min]
    end
    
    Q --> QW
    HIST --> QW
    RETRY --> QW
    QSPEC --> QW
    
    QW --> BM25
    QW --> VEC
    
    BM25 --> RRF
    VEC --> RRF
    
    RRF --> RERANK
    RERANK --> EXCLUDE
    EXCLUDE --> DIVERSITY
    
    DIVERSITY --> EVIDENCE[EvidencePack]
```

---

## 8. Main Data Structures

### QuerySpec (Normalizer output)
```yaml
intent: string
entities: list
constraints: dict
required_evidence: list  # numbers, links, transaction_link, policy, steps
risk_level: low | medium | high
keyword_queries: list
semantic_queries: list
clarifying_questions: list
is_ambiguous: bool
skip_retrieval: bool
canned_response: string | null
```

### DecisionResult (Decision Router output)
```yaml
decision: PASS | ASK_USER | ESCALATE
reason: string
clarifying_questions: list
partial_links: list
answer: string  # pre-generated when ASK_USER/ESCALATE
```

### AnswerOutput (AnswerService output)
```yaml
decision: PASS | ASK_USER | ESCALATE
answer: string
followup_questions: list
citations: list
confidence: float
debug: dict
```

---

## 9. Exit Points (no LLM call)

1. **Intent cache hit** → return immediately
2. **skip_retrieval** (greeting) → return canned_response
3. **is_ambiguous** → Decision Router → ASK_USER
4. **empty evidence** → ASK_USER
5. **Decision Router ≠ PASS** → ASK_USER or ESCALATE
6. **LLM error** → ESCALATE

---

## 10. Exit Points (with LLM call)

1. **Reviewer PASS** → return answer + citations
2. **Reviewer ASK_USER** → return
3. **Reviewer ESCALATE** → return
4. **Reviewer RETRIEVE_MORE** + attempt < max → retry loop
5. **Max attempts reached** → return ASK_USER

---

*Document generated from current codebase.*  
*References: `app/services/answer_service.py`, `app/archi.md`*
