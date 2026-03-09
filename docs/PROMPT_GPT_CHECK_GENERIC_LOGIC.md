# Prompt: GPT Check and Fix Non-Generic Logic

Copy the prompt below and send it to GPT (or another AI) along with repo context.

---

## PROMPT

```
You need to audit and fix the RAG chatbot project (auto-reply-chatbot) to ensure logic is **generic** and free of hardcoded domain-specific rules. The project must comply with `.cursor/rules/project-development.mdc`.

### Mandatory Principles (project-development.mdc)

1. **No hardcoded data:** Company name, URLs, prices, product names, policy text → config/env/DB
2. **No narrow rules:** Logic must scale across domains (VPS, e-commerce, SaaS, healthcare)
3. **Rule data extensible:** Fixed lists, keyword lists → config-driven
4. **No rigid keyword rules for intent/evidence:** LLM is the orchestrator; no keyword matching
5. **No rule-based evidence quality:** Regex/string checks for quality → LLM evaluates

### Tasks

**Step 1 – Audit:** Find all violations:
- Hardcoded regex for refund, policy, pricing, VPS, high-risk
- Keyword lists: ["refund", "policy", "terms", "eligible", "vps", "billing", "order", "store"]
- Intent patterns that bypass RAG (refund_policy intent returns canned answer)
- Doc type inference from URL (vps, billing, store → pricing)

**Step 2 – List:** Output a table:
| File | Line | Violation | Recommendation |
|------|------|-----------|----------------|

**Step 3 – Fix (in priority order):**

1. **reviewer.py** – HIGH_RISK_PATTERNS, policy_phrases, _has_policy_citation
   - Move patterns to config: `reviewer_high_risk_patterns` (env or DB)
   - Or: remove heuristic, use LLM to detect high-risk claims

2. **claim_parser.py** – policy_phrases, is_policy_claim
   - Move to config `claim_parser_policy_patterns`
   - Or: LLM classify claim type

3. **evidence_set_builder.py** – _chunk_satisfies_requirement with ["policy", "terms", "refund", "eligible"]
   - Prefer coverage_map from Evidence Selector (LLM)
   - When absent: config `evidence_requirement_keywords: dict[str, list[str]]` or remove heuristic

4. **evidence_hygiene.py** – BOILERPLATE_PATTERNS, TRANSACTION_PATH_PATTERN
   - TRANSACTION_PATH_PATTERN contains vps, dedicated, billing → VPS-specific
   - Move to config `hygiene_transaction_path_patterns`

5. **branding_config.py** – fallback intent refund_policy
   - Remove or move to DB (intents table). Do not bypass RAG for refund queries.
   - Fallback answer should be generic: "I'll search our docs. What specific aspect do you need?" or let it go through RAG

6. **web_crawler.py, source_loaders.py** – _infer_doc_type from URL
   - "vps", "billing", "store" → pricing: VPS-specific
   - Move to config `doc_type_url_keywords: dict[str, list[str]]` (doc_type → keywords)

7. **retrieval_planner.py** – strong = {"policy_language", "transaction_link", "steps_structure"}
   - Can keep (these are standard evidence types) but ensure no additional hardcoding

8. **normalizer.py** – NORMALIZER_SYSTEM_PROMPT
   - Add guidance: "Use only standard evidence types: policy_language, numbers_units, transaction_link, steps_structure, has_any_url. Do not invent types like 'promo plan details'."

9. **evidence_quality.py** – EVIDENCE_QUALITY_PROMPT
   - Add: "When evidence states exclusion (e.g. 'special discounted plans excluded'), treat promo/discount as synonymous. Set is_sufficient=true if policy answers the question."

### Constraints

- Do not add new keyword rules
- All changes via: config (app/core/config.py), env, DB, or prompt
- Preserve backward compatibility: config defaults should match current behavior
- Run tests after changes: `pytest tests/ -v`

### Reference docs in repo

- `.cursor/rules/project-development.mdc` – principles
- `docs/RESEARCH_EVIDENCE_REQUIREMENTS_OVER_SPECIFICATION.md` – detailed problem and fix directions
```

---

## How to Use

1. Open the repo in Cursor/IDE
2. Copy the entire prompt content (from "You need to audit..." to "...fix directions")
3. Paste into ChatGPT/Claude/Cursor Chat
4. Optionally add: "This is a Python FastAPI codebase. Structure: app/services/ contains normalizer, reviewer, evidence_quality, claim_parser, etc."
5. If GPT has no repo access: attach or paste the relevant files (reviewer.py, claim_parser.py, evidence_set_builder.py, branding_config.py, project-development.mdc)

---

## Expected Output

1. **Audit table** – list of violations
2. **Patch/diff** – code changes per file
3. **Config additions** – new fields for config.py and .env.example
4. **Test** – confirmation that pytest was run with no regressions
