"""
Prompt templates for the Gemini-based relevancy scorer.

These prompts are designed to produce structured JSON output with
multi-dimensional scoring for job–resume matching.
"""

# ---------------------------------------------------------------------------
# Main scoring prompt
# ---------------------------------------------------------------------------

SCORING_PROMPT = """\
You are an expert career advisor specialising in the Indian job market.

**TASK**: Score how well the candidate's resume matches the given job description.

---

### RESUME
{resume_text}

---

### JOB DETAILS
- **Title**: {job_title}
- **Company**: {company}
- **Location**: {location}
- **Description**:
{job_description}

---

### TARGET PROFILE
The candidate is targeting **Product Manager** roles at **Fintech and Banking** \
companies in India.

### SCORING DIMENSIONS (score each 0–100)

1. **skills_match** — What percentage of the skills required in the JD are \
present (explicitly or implicitly) in the resume? Consider product strategy, \
analytics, stakeholder management, technical skills, tools, etc.

2. **domain_fit** — How well does the candidate's domain experience align with \
the company's domain? Fintech (payments, lending, neo-banking, insurance-tech, \
wealth-tech, crypto) and Banking/NBFC (retail banking, corporate banking, \
credit cards, digital banking) score highest.

3. **experience_match** — Does the candidate's years of experience match the \
job's requirements? Score 100 for a perfect match, penalise both \
over-qualification and under-qualification.

4. **seniority_match** — Does the role's seniority level (APM, PM, Senior PM, \
Lead PM, Group PM, Director, VP) match the candidate's career stage?

### ADDITIONAL OUTPUT

- **missing_skills**: a list of 3–7 specific skills from the JD that are \
absent from the resume (so the candidate knows what to highlight or learn).

- **verdict**: one of "STRONG_FIT", "GOOD_FIT", "PARTIAL_FIT", "WEAK_FIT".

- **apply_priority**: one of "APPLY_NOW", "REVIEW_FIRST", "SKIP".

- **reasoning**: a concise 2-sentence explanation of your overall assessment.\
"""

# ---------------------------------------------------------------------------
# Company classification prompt (fallback when company is not in curated list)
# ---------------------------------------------------------------------------

COMPANY_CLASSIFICATION_PROMPT = """\
Classify the following company into ONE of these categories based on its \
primary business:

- **fintech** — payments, lending, neo-banking, insurance-tech, wealth-tech, \
crypto/blockchain, POS terminals, payment gateways, buy-now-pay-later, \
credit scoring, digital wallets
- **bank** — scheduled commercial bank (public or private sector), \
foreign bank operating in India
- **nbfc** — non-banking financial company, housing finance, \
micro-finance institution, gold loan company
- **digital_banking_arm** — digital banking product or subsidiary of a \
traditional bank (e.g. YONO by SBI, iMobile by ICICI)
- **other** — none of the above

Company name: {company_name}
Job title: {job_title}
Job description snippet (first 300 chars): {description_snippet}

Respond with ONLY the category label (one word, lowercase). Do not add any \
explanation.\
"""
