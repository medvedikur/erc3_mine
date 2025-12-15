# Test Run Summary: parallel_YYYYMMDD_HHMMSS

**Date:** YYYY-MM-DD HH:MM
**Model:** model_name
**Total Tests:** N

---

## Overall Statistics

| Metric | Value |
|--------|-------|
| **Tests Completed** | X/N (%) |
| **Final Response Submitted** | X (%) |
| **Hit Turn Limit (20)** | X (list files) |
| **Logs with `links` array** | X |
| **Guard warnings** | X |

---

## Outcome Distribution

| Outcome | Count | Percentage |
|---------|-------|------------|
| `ok_answer` | X | X% |
| `denied_security` | X | X% |
| `ok_not_found` | X | X% |
| `none_clarification_needed` | X | X% |

---

## Critical Issues

### 1. Issue Name
**Severity:** HIGH/MEDIUM/LOW
**Impact:** Description
**Root cause:** Explanation

---

## Sample Tasks (first 10)

| Test | Question | Outcome |
|------|----------|---------|
| t000 | Question text... | outcome |

---

## Recommendations

### Priority 1: Most critical fix
- **File:** affected file
- **Issue:** description
- **Solution:** proposed fix

---

## Comparison with Previous Run (if available)

| Metric | Previous | Current | Delta |
|--------|----------|---------|-------|
| Completed | X | Y | +/-Z |

---

*Generated: YYYY-MM-DD*

---

## Data Extraction Commands

```bash
# Count completed
grep -l "FINAL RESPONSE SUBMITTED" logs/parallel_*/t*.log | wc -l

# Find blocked (no final response)
grep -c "FINAL RESPONSE SUBMITTED" logs/parallel_*/t*.log | grep ":0$"

# Outcome distribution
grep -h "outcome" logs/parallel_*/t*.log | grep -o '"outcome": "[^"]*"' | sort | uniq -c

# Check links presence
grep -l '"links":' logs/parallel_*/t*.log | wc -l

# Find turn limit hits
grep -l "Turn 20/20" logs/parallel_*/t*.log
```
