# BYO Agent Configuration — Field Values

> Use these exact values when filling in the form on Prompt Opinion's "Build your own agents" page.

---

## Agent — top-level fields

**Agent name**
```
TrialMatch & Triage AI
```

**Agent description**
```
Decision-support agent that answers two questions for clinicians and patients: which active clinical trials might fit this patient (MATCH), and how urgently should this patient be considered for trial enrollment (TRIAGE). Reads live FHIR data from the workspace, classifies enrollment urgency, searches ClinicalTrials.gov, and explains eligibility against the patient's chart in plain language.
```

**Context type**
```
Patient
```

**Grounding / collection**
```
None
```
*(Live API data via MCP tools — no static collection needed.)*

**System prompt**
```
[Paste the entire contents of agent_config/system_prompt.md here.]
```

**A2A — Enable for agent-to-agent invocation**
```
✅ ON
```

**FHIR context propagation**
```
✅ ON
```

---

## Skill — primary capability

**Skill name**
```
match_and_triage_clinical_trials
```

**Skill description**
```
Given a patient in context, pull their FHIR data, classify enrollment urgency (URGENT / MODERATE / EXPLORATORY), search ClinicalTrials.gov for matching trials, evaluate inclusion/exclusion criteria against the chart, and return a ranked list of the top 3-5 matches with patient-friendly explanations and verification flags. Invoke when a user asks: "find clinical trials for this patient", "is there a trial for [condition]", "match and triage this patient", "how urgent is trial enrollment for this patient", "what research is available for...".
```

---

## Tools — attach the MCP

After saving the agent once, edit it again and attach all six tools from your `TrialMatch & Triage MCP` server:

- ✅ `read_patient_fhir`
- ✅ `triage_patient_urgency`
- ✅ `search_clinical_trials`
- ✅ `match_trials_to_patient`
- ✅ `extract_eligibility_signals`
- ✅ `search_pubmed_research`
