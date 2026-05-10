"""
TrialMatch & Triage — MCP Server with Prompt Opinion FHIR context support
=========================================================================

Healthcare AI server that:
  • MATCHES patients to ClinicalTrials.gov studies
  • TRIAGES which patients are urgent vs exploratory trial candidates
  • Reads live FHIR data from the Prompt Opinion workspace via SHARP context

This server speaks MCP over Streamable HTTP at POST /mcp and declares
support for Prompt Opinion's `ai.promptopinion/fhir-context` extension —
so it shows up correctly when added in Workspace Hub → MCP Servers.

Tools:
    1. read_patient_fhir            → Pull live patient data from workspace FHIR
    2. search_clinical_trials       → Find trials by condition + location
    3. match_trials_to_patient      → Score trials against patient profile
    4. extract_eligibility_signals  → Parse one trial's inclusion/exclusion text
    5. triage_patient_urgency       → Classify enrollment urgency tier
    6. search_pubmed_research       → Supporting biomedical research

Run:
    python trialmatch_mcp.py
Default URL:  http://0.0.0.0:8000/mcp
Expose with:  ngrok http 8000
"""

import inspect
import json
import os
import re
from contextvars import ContextVar
from typing import Any, Optional, List

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


# ─────────────────────────────────────────────────────────────────────────
# Server config
# ─────────────────────────────────────────────────────────────────────────
PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")

CT_BASE = "https://clinicaltrials.gov/api/v2"
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

USER_AGENT = "TrialMatch-Triage/1.0 (Prompt Opinion Hackathon)"
HTTP_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# Per-request FHIR context, captured from headers on each tool call
fhir_ctx: ContextVar[dict] = ContextVar("fhir_ctx", default={})


# ─────────────────────────────────────────────────────────────────────────
# Prompt Opinion FHIR extension declaration (declared in initialize response)
# ─────────────────────────────────────────────────────────────────────────
PO_FHIR_EXTENSION = {
    "ai.promptopinion/fhir-context": {
        "scopes": [
            {"name": "patient/Patient.rs", "required": False},
            {"name": "patient/Condition.rs", "required": False},
            {"name": "patient/Observation.rs", "required": False},
            {"name": "patient/MedicationRequest.rs", "required": False},
            {"name": "patient/AllergyIntolerance.rs", "required": False},
        ]
    }
}


# ─────────────────────────────────────────────────────────────────────────
# Tool helpers
# ─────────────────────────────────────────────────────────────────────────
def _parse_age(age_str: str, default: int) -> int:
    """Parse '18 Years' / '6 Months' / 'N/A' into integer years."""
    if not age_str or age_str.upper() == "N/A":
        return default
    parts = age_str.strip().split()
    if not parts:
        return default
    try:
        value = int(parts[0])
    except (ValueError, TypeError):
        return default
    unit = parts[1].lower() if len(parts) > 1 else "years"
    if "year" in unit:
        return value
    if "month" in unit:
        return max(0, value // 12)
    return 0


def _split_inclusion_exclusion(text: str) -> dict:
    """Split free-text eligibility criteria into bullet lists."""
    if not text:
        return {"inclusion": [], "exclusion": []}
    incl_text, excl_text = text, ""
    excl_match = re.search(r"exclusion\s*criteria\s*[:\-]?", text, re.IGNORECASE)
    if excl_match:
        incl_text = text[: excl_match.start()]
        excl_text = text[excl_match.end():]
    incl_match = re.search(r"inclusion\s*criteria\s*[:\-]?", incl_text, re.IGNORECASE)
    if incl_match:
        incl_text = incl_text[incl_match.end():]

    def _bullets(s: str) -> List[str]:
        items = re.split(r"\n\s*[-•*●▪]\s*|\n\s*\d+[\.\)]\s*|\n\s*\n", s)
        return [it.strip() for it in items if it.strip() and len(it.strip()) > 5]

    return {
        "inclusion": _bullets(incl_text)[:25],
        "exclusion": _bullets(excl_text)[:25],
    }


async def _fhir_get(resource_path: str) -> dict:
    """GET a resource from the workspace FHIR server using context headers."""
    ctx = fhir_ctx.get()
    fhir_url = ctx.get("fhir_url")
    if not fhir_url:
        return {"error": "No FHIR server URL in context. Did you enable FHIR context for this MCP server?"}
    token = ctx.get("access_token")
    headers = dict(HTTP_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = fhir_url.rstrip("/")
    url = f"{base}/{resource_path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        return {"error": f"FHIR request failed for {url}: {e}"}


# ─────────────────────────────────────────────────────────────────────────
# TOOL 1 — Read live patient data from the workspace FHIR server
# ─────────────────────────────────────────────────────────────────────────
async def read_patient_fhir() -> dict:
    """Pull live demographics, conditions, recent observations, medications, and allergies for the patient currently in context.

    Uses the FHIR server URL, access token, and patient ID passed in by Prompt Opinion's
    SHARP context. Always call this FIRST so subsequent matching/triage tools have an
    accurate, authoritative patient profile.
    """
    ctx = fhir_ctx.get()
    pid = ctx.get("patient_id")
    if not pid:
        return {
            "error": "No patient in context. Select a patient in the Prompt Opinion launchpad before calling this tool.",
        }

    patient = await _fhir_get(f"Patient/{pid}")
    conditions_bundle = await _fhir_get(f"Condition?patient={pid}&_count=50")
    observations_bundle = await _fhir_get(
        f"Observation?patient={pid}&_count=20&_sort=-date"
    )
    meds_bundle = await _fhir_get(f"MedicationRequest?patient={pid}&_count=50")
    allergies_bundle = await _fhir_get(f"AllergyIntolerance?patient={pid}&_count=50")

    # Demographics
    name = ""
    if isinstance(patient.get("name"), list) and patient["name"]:
        n = patient["name"][0]
        name = " ".join(n.get("given", []) + [n.get("family", "")]).strip()
    address_city = ""
    address_state = ""
    if isinstance(patient.get("address"), list) and patient["address"]:
        a = patient["address"][0]
        address_city = a.get("city", "")
        address_state = a.get("state", "")

    # Helper to read entries from a Bundle
    def _entries(bundle: dict) -> list:
        if not isinstance(bundle, dict):
            return []
        return [e.get("resource", {}) for e in bundle.get("entry", []) or []]

    conditions = []
    for c in _entries(conditions_bundle):
        text = (c.get("code", {}) or {}).get("text") or ""
        codings = (c.get("code", {}) or {}).get("coding", []) or []
        if not text and codings:
            text = codings[0].get("display", "") or ""
        clinical = (c.get("clinicalStatus", {}) or {}).get("coding", [{}])
        clinical_status = clinical[0].get("code", "") if clinical else ""
        conditions.append({"name": text, "status": clinical_status})

    observations = []
    for o in _entries(observations_bundle):
        code_text = (o.get("code", {}) or {}).get("text") or ""
        if not code_text:
            codings = (o.get("code", {}) or {}).get("coding", []) or []
            code_text = codings[0].get("display", "") if codings else ""
        value = ""
        if "valueQuantity" in o:
            vq = o["valueQuantity"]
            value = f"{vq.get('value', '')} {vq.get('unit', '')}".strip()
        elif "valueString" in o:
            value = o["valueString"]
        elif "valueCodeableConcept" in o:
            value = (o["valueCodeableConcept"].get("text", "") or "")
        observations.append(
            {"name": code_text, "value": value, "date": o.get("effectiveDateTime", "")}
        )

    medications = []
    for m in _entries(meds_bundle):
        med_text = ""
        mcc = m.get("medicationCodeableConcept", {}) or {}
        med_text = mcc.get("text") or ""
        if not med_text:
            codings = mcc.get("coding", []) or []
            med_text = codings[0].get("display", "") if codings else ""
        status = m.get("status", "")
        medications.append({"name": med_text, "status": status})

    allergies = []
    for a in _entries(allergies_bundle):
        ac = a.get("code", {}) or {}
        a_text = ac.get("text") or ""
        if not a_text:
            codings = ac.get("coding", []) or []
            a_text = codings[0].get("display", "") if codings else ""
        allergies.append({"substance": a_text})

    return {
        "patient_id": pid,
        "name": name,
        "gender": patient.get("gender", ""),
        "birth_date": patient.get("birthDate", ""),
        "city": address_city,
        "state": address_state,
        "active_conditions": [c for c in conditions if c["status"] in ("active", "")][:15],
        "recent_observations": observations[:10],
        "active_medications": [m for m in medications if m["status"] == "active"][:15],
        "allergies": allergies[:10],
    }


# ─────────────────────────────────────────────────────────────────────────
# TOOL 2 — Search ClinicalTrials.gov
# ─────────────────────────────────────────────────────────────────────────
async def search_clinical_trials(
    condition: str,
    location: Optional[str] = None,
    status: str = "RECRUITING",
    max_results: int = 10,
) -> dict:
    """Search ClinicalTrials.gov for studies matching a condition (and optional location).

    Args:
        condition: Disease or condition (e.g. 'HER2 positive breast cancer').
        location: Optional city/state/country filter.
        status: Default RECRUITING. Also: ACTIVE_NOT_RECRUITING, NOT_YET_RECRUITING.
        max_results: 1–50, default 10.
    """
    max_results = max(1, min(int(max_results), 50))
    params: dict = {
        "query.cond": condition,
        "filter.overallStatus": status,
        "pageSize": max_results,
        "format": "json",
    }
    if location:
        params["query.locn"] = location

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=HTTP_HEADERS) as client:
            r = await client.get(f"{CT_BASE}/studies", params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        return {"error": f"ClinicalTrials.gov request failed: {e}", "count": 0, "trials": []}

    trials: list = []
    for study in data.get("studies", []):
        proto = study.get("protocolSection", {}) or {}
        ident = proto.get("identificationModule", {}) or {}
        st = proto.get("statusModule", {}) or {}
        desc = proto.get("descriptionModule", {}) or {}
        elig = proto.get("eligibilityModule", {}) or {}
        contacts = proto.get("contactsLocationsModule", {}) or {}
        conds = proto.get("conditionsModule", {}) or {}
        design = proto.get("designModule", {}) or {}

        nct_id = ident.get("nctId", "")
        trials.append({
            "nct_id": nct_id,
            "title": ident.get("briefTitle", ""),
            "status": st.get("overallStatus", ""),
            "phase": design.get("phases", []),
            "conditions": (conds.get("conditions", []) or [])[:5],
            "brief_summary": (desc.get("briefSummary", "") or "")[:300],
            "min_age": elig.get("minimumAge", ""),
            "max_age": elig.get("maximumAge", ""),
            "sex": elig.get("sex", ""),
            "locations": [
                {
                    "facility": loc.get("facility", ""),
                    "city": loc.get("city", ""),
                    "state": loc.get("state", ""),
                    "country": loc.get("country", ""),
                }
                for loc in (contacts.get("locations", []) or [])[:2]
            ],
            "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else "",
        })

    return {"count": len(trials), "trials": trials}


# ─────────────────────────────────────────────────────────────────────────
# TOOL 3 — Match (rank trials against patient)
# ─────────────────────────────────────────────────────────────────────────
async def match_trials_to_patient(
    nct_ids: List[str],
    patient_age: int,
    patient_sex: str,
    patient_conditions: List[str],
    top_n: int = 8,
) -> dict:
    """Rank candidate trials against a patient's age, sex, and condition list. Hard-filters mismatches and scores compatibility 0–100. Returns only the top N results.

    Args:
        nct_ids: NCT IDs from search_clinical_trials (caps at 20).
        patient_age: Age in years.
        patient_sex: 'MALE', 'FEMALE', or 'ALL'.
        patient_conditions: Diagnosed conditions, lower-case keywords ok.
        top_n: How many top-ranked trials to return (default 8, max 15).
    """
    top_n = max(1, min(int(top_n), 15))
    sex_norm = (patient_sex or "ALL").upper()
    pt_conds = [c.lower() for c in (patient_conditions or [])]
    ranked: list = []

    async with httpx.AsyncClient(timeout=30.0, headers=HTTP_HEADERS) as client:
        for nct_id in (nct_ids or [])[:20]:
            try:
                r = await client.get(f"{CT_BASE}/studies/{nct_id}", params={"format": "json"})
                if r.status_code != 200:
                    ranked.append({
                        "nct_id": nct_id, "basic_eligible": False,
                        "match_score": 0,
                        "reasons": [f"Could not fetch (HTTP {r.status_code})"],
                    })
                    continue
                proto = r.json().get("protocolSection", {}) or {}
                elig = proto.get("eligibilityModule", {}) or {}
                conds = proto.get("conditionsModule", {}) or {}

                trial_sex = (elig.get("sex", "ALL") or "ALL").upper()
                min_age = _parse_age(elig.get("minimumAge", ""), default=0)
                max_age = _parse_age(elig.get("maximumAge", ""), default=200)

                eligible = True
                reasons: List[str] = []
                score = 50

                if patient_age < min_age:
                    eligible = False; score -= 30
                    reasons.append(f"❌ Age {patient_age} below minimum {min_age}")
                elif patient_age > max_age:
                    eligible = False; score -= 30
                    reasons.append(f"❌ Age {patient_age} above maximum {max_age}")
                else:
                    score += 15
                    reasons.append(f"✓ Age {patient_age} in range {min_age}–{max_age}")

                if trial_sex != "ALL" and sex_norm != "ALL" and trial_sex != sex_norm:
                    eligible = False; score -= 30
                    reasons.append(f"❌ Trial requires {trial_sex}, patient is {sex_norm}")
                else:
                    score += 10
                    reasons.append(f"✓ Sex requirement compatible ({trial_sex})")

                trial_conds = [c.lower() for c in (conds.get("conditions", []) or [])]
                matches = sum(
                    1 for pc in pt_conds for tc in trial_conds
                    if pc and tc and (pc in tc or tc in pc)
                )
                if matches:
                    score += min(25, matches * 10)
                    reasons.append(f"✓ Matches {matches} of patient's conditions")

                ranked.append({
                    "nct_id": nct_id,
                    "basic_eligible": eligible,
                    "match_score": max(0, min(100, score)),
                    "reasons": reasons,
                })
            except Exception as e:
                ranked.append({
                    "nct_id": nct_id, "basic_eligible": False,
                    "match_score": 0, "reasons": [f"Error: {e}"],
                })

    ranked.sort(key=lambda x: x["match_score"], reverse=True)
    eligible_count = sum(1 for r in ranked if r["basic_eligible"])
    return {
        "total_evaluated": len(ranked),
        "eligible_count": eligible_count,
        "ranked_trials": ranked[:top_n],
    }


# ─────────────────────────────────────────────────────────────────────────
# TOOL 4 — Extract eligibility signals from one trial
# ─────────────────────────────────────────────────────────────────────────
async def extract_eligibility_signals(nct_id: str) -> dict:
    """Return one trial's title and eligibility text split into inclusion/exclusion bullet lists.

    Args:
        nct_id: NCT ID (e.g., 'NCT04567890').
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=HTTP_HEADERS) as client:
            r = await client.get(f"{CT_BASE}/studies/{nct_id}", params={"format": "json"})
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        return {"error": f"Failed to fetch {nct_id}: {e}"}

    proto = data.get("protocolSection", {}) or {}
    ident = proto.get("identificationModule", {}) or {}
    elig = proto.get("eligibilityModule", {}) or {}
    contacts = proto.get("contactsLocationsModule", {}) or {}

    raw = elig.get("eligibilityCriteria", "") or ""
    split = _split_inclusion_exclusion(raw)

    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "min_age": elig.get("minimumAge", ""),
        "max_age": elig.get("maximumAge", ""),
        "sex": elig.get("sex", ""),
        "healthy_volunteers": elig.get("healthyVolunteers", ""),
        "inclusion": split["inclusion"][:12],
        "exclusion": split["exclusion"][:12],
        "central_contacts": [
            {
                "name": c.get("name", ""), "phone": c.get("phone", ""),
                "email": c.get("email", ""),
            }
            for c in (contacts.get("centralContacts", []) or [])[:1]
        ],
        "first_locations": [
            {
                "facility": loc.get("facility", ""), "city": loc.get("city", ""),
                "state": loc.get("state", ""), "country": loc.get("country", ""),
            }
            for loc in (contacts.get("locations", []) or [])[:3]
        ],
        "url": f"https://clinicaltrials.gov/study/{ident.get('nctId', '')}",
    }


# ─────────────────────────────────────────────────────────────────────────
# TOOL 5 — Triage: classify enrollment urgency for the patient
# ─────────────────────────────────────────────────────────────────────────
async def triage_patient_urgency(
    primary_condition: str,
    prior_treatments_tried: int,
    disease_status: str,
    has_progression: bool,
    standard_options_remaining: bool,
) -> dict:
    """Classify how urgently this patient should be considered for clinical-trial enrollment.

    Returns one of: URGENT, MODERATE, EXPLORATORY — with a rationale and recommended next steps.
    Use this so a clinician knows whether to fast-track this patient for trial review or just
    keep them on a watch-list.

    Args:
        primary_condition: Primary condition name.
        prior_treatments_tried: Count of prior systemic regimens or biologics tried.
        disease_status: 'progressive', 'stable', 'responding', or 'newly_diagnosed'.
        has_progression: True if recent imaging/labs show disease progression.
        standard_options_remaining: True if proven standard-of-care options still exist.
    """
    status = (disease_status or "").lower().strip()
    score = 0
    drivers: list = []

    if has_progression:
        score += 35
        drivers.append("Active disease progression on current therapy")
    if status == "progressive":
        score += 20
        drivers.append("Documented progressive disease state")
    if not standard_options_remaining:
        score += 30
        drivers.append("Standard-of-care options exhausted")
    if prior_treatments_tried >= 3:
        score += 15
        drivers.append(f"Heavily pre-treated ({prior_treatments_tried} prior regimens)")
    elif prior_treatments_tried == 2:
        score += 8
        drivers.append("Multiple prior regimens tried")

    if status == "responding":
        score -= 15
        drivers.append("Currently responding to therapy — less time-pressure")
    if status == "newly_diagnosed":
        score -= 10
        drivers.append("Newly diagnosed — standard pathway should be evaluated first")

    score = max(0, min(100, score))

    if score >= 65:
        tier = "URGENT"
        recommendation = (
            "Fast-track for trial-team consultation within 1–2 weeks. "
            "Prioritize trials accepting recent progression and pre-treated patients."
        )
    elif score >= 35:
        tier = "MODERATE"
        recommendation = (
            "Begin trial matching now; aim for trial-team consultation within 4–6 weeks. "
            "Consider trials with broader inclusion criteria."
        )
    else:
        tier = "EXPLORATORY"
        recommendation = (
            "Patient stable; trial matching can run in parallel with current therapy. "
            "Build a watch-list of trials and re-assess at next surveillance scan."
        )

    return {
        "urgency_tier": tier,
        "urgency_score": score,
        "primary_condition": primary_condition,
        "drivers": drivers,
        "recommendation": recommendation,
        "disclaimer": "Triage is decision-support only; final urgency assessment is the treating clinician's.",
    }


# ─────────────────────────────────────────────────────────────────────────
# TOOL 6 — PubMed research
# ─────────────────────────────────────────────────────────────────────────
async def search_pubmed_research(query: str, max_results: int = 5) -> dict:
    """Search PubMed for biomedical research articles related to a topic.

    Args:
        query: Search terms.
        max_results: 1–20, default 5.
    """
    max_results = max(1, min(int(max_results), 20))
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=HTTP_HEADERS) as client:
            r1 = await client.get(
                f"{PUBMED_BASE}/esearch.fcgi",
                params={
                    "db": "pubmed", "term": query, "retmax": max_results,
                    "retmode": "json", "sort": "relevance",
                },
            )
            r1.raise_for_status()
            ids = r1.json().get("esearchresult", {}).get("idlist", []) or []
            if not ids:
                return {"count": 0, "articles": []}
            r2 = await client.get(
                f"{PUBMED_BASE}/esummary.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
            )
            r2.raise_for_status()
            result = r2.json().get("result", {}) or {}
    except httpx.HTTPError as e:
        return {"error": f"PubMed query failed: {e}", "count": 0, "articles": []}

    articles = []
    for pmid in ids:
        a = result.get(pmid, {}) or {}
        if a:
            articles.append({
                "pmid": pmid,
                "title": a.get("title", ""),
                "authors": [au.get("name", "") for au in (a.get("authors", []) or [])][:5],
                "journal": a.get("source", ""),
                "pub_date": a.get("pubdate", ""),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })

    return {"count": len(articles), "articles": articles}


# ─────────────────────────────────────────────────────────────────────────
# Tool registry → JSON Schema for MCP tools/list
# ─────────────────────────────────────────────────────────────────────────
TOOLS: dict = {
    "read_patient_fhir": {
        "fn": read_patient_fhir,
        "schema": {
            "type": "object",
            "properties": {},
        },
    },
    "search_clinical_trials": {
        "fn": search_clinical_trials,
        "schema": {
            "type": "object",
            "properties": {
                "condition": {"type": "string", "description": "Disease or condition (e.g., 'HER2 positive breast cancer')."},
                "location": {"type": "string", "description": "Optional city/state/country filter."},
                "status": {"type": "string", "description": "Default RECRUITING.", "default": "RECRUITING"},
                "max_results": {"type": "integer", "description": "1–50, default 10.", "default": 10},
            },
            "required": ["condition"],
        },
    },
    "match_trials_to_patient": {
        "fn": match_trials_to_patient,
        "schema": {
            "type": "object",
            "properties": {
                "nct_ids": {"type": "array", "items": {"type": "string"}, "description": "NCT IDs (caps at 20)."},
                "patient_age": {"type": "integer"},
                "patient_sex": {"type": "string", "description": "'MALE', 'FEMALE', or 'ALL'."},
                "patient_conditions": {"type": "array", "items": {"type": "string"}},
                "top_n": {"type": "integer", "description": "How many top-ranked trials to return (default 8, max 15).", "default": 8},
            },
            "required": ["nct_ids", "patient_age", "patient_sex", "patient_conditions"],
        },
    },
    "extract_eligibility_signals": {
        "fn": extract_eligibility_signals,
        "schema": {
            "type": "object",
            "properties": {
                "nct_id": {"type": "string", "description": "NCT ID, e.g., 'NCT04567890'."},
            },
            "required": ["nct_id"],
        },
    },
    "triage_patient_urgency": {
        "fn": triage_patient_urgency,
        "schema": {
            "type": "object",
            "properties": {
                "primary_condition": {"type": "string"},
                "prior_treatments_tried": {"type": "integer"},
                "disease_status": {"type": "string", "description": "'progressive', 'stable', 'responding', 'newly_diagnosed'."},
                "has_progression": {"type": "boolean"},
                "standard_options_remaining": {"type": "boolean"},
            },
            "required": ["primary_condition", "prior_treatments_tried", "disease_status", "has_progression", "standard_options_remaining"],
        },
    },
    "search_pubmed_research": {
        "fn": search_pubmed_research,
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
}


def _tool_descriptions() -> list:
    """Build the MCP tools/list response payload."""
    out = []
    for name, spec in TOOLS.items():
        doc = inspect.getdoc(spec["fn"]) or ""
        out.append({
            "name": name,
            "description": doc.strip(),
            "inputSchema": spec["schema"],
        })
    return out


# ─────────────────────────────────────────────────────────────────────────
# JSON-RPC dispatcher
# ─────────────────────────────────────────────────────────────────────────
PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "TrialMatch & Triage", "version": "1.0.0"}


async def _handle_jsonrpc(message: dict) -> Optional[dict]:
    """Handle a single JSON-RPC request or notification. Returns None for notifications."""
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}

    # Notifications (no response expected)
    if msg_id is None:
        return None

    # initialize
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": SERVER_INFO,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "extensions": PO_FHIR_EXTENSION,
                },
            },
        }

    # tools/list
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": _tool_descriptions()},
        }

    # tools/call
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        spec = TOOLS.get(name)
        if not spec:
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown tool '{name}'"},
            }
        try:
            result = await spec["fn"](**args)
        except TypeError as e:
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32602, "message": f"Invalid arguments for {name}: {e}"},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32000, "message": f"Tool {name} failed: {e}"},
            }
        return {
            "jsonrpc": "2.0", "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, default=str)}],
                "isError": False,
            },
        }

    # ping
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    return {
        "jsonrpc": "2.0", "id": msg_id,
        "error": {"code": -32601, "message": f"Method '{method}' not found"},
    }


# ─────────────────────────────────────────────────────────────────────────
# Starlette HTTP layer (MCP Streamable HTTP transport)
# ─────────────────────────────────────────────────────────────────────────
async def mcp_endpoint(request: Request) -> Response:
    """Single endpoint for MCP Streamable HTTP at POST /mcp."""
    # Capture FHIR context from headers for use in tools
    fhir_ctx.set({
        "fhir_url": request.headers.get("x-fhir-server-url", ""),
        "access_token": request.headers.get("x-fhir-access-token", ""),
        "patient_id": request.headers.get("x-patient-id", ""),
    })

    if request.method == "GET":
        # No server-initiated SSE stream from this server
        return Response(status_code=405)

    if request.method == "DELETE":
        return Response(status_code=204)

    if request.method != "POST":
        return Response(status_code=405)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )

    # JSON-RPC supports batches
    if isinstance(body, list):
        responses = []
        for msg in body:
            resp = await _handle_jsonrpc(msg)
            if resp is not None:
                responses.append(resp)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses)

    # Single JSON-RPC message
    if not isinstance(body, dict):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32600, "message": "Invalid request"}},
            status_code=400,
        )

    resp = await _handle_jsonrpc(body)
    if resp is None:
        # Notification — no body, just 202 Accepted
        return Response(status_code=202)
    return JSONResponse(resp)


async def health_endpoint(request: Request) -> Response:
    return JSONResponse({
        "ok": True,
        "server": SERVER_INFO,
        "tools": list(TOOLS.keys()),
        "fhir_extension": list(PO_FHIR_EXTENSION.keys())[0],
    })


app = Starlette(routes=[
    Route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE"]),
    Route("/health", health_endpoint, methods=["GET"]),
])


# ─────────────────────────────────────────────────────────────────────────
# Boot
# ─────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 64)
    print("🚀 TrialMatch & Triage — MCP Server")
    print("=" * 64)
    print(f"  Listening on:  http://{HOST}:{PORT}/mcp")
    print(f"  Health check:  http://{HOST}:{PORT}/health")
    print(f"  Tools:")
    for t in TOOLS:
        print(f"    • {t}")
    print(f"  FHIR extension: ai.promptopinion/fhir-context  ✅")
    print(f"  Expose with:   ngrok http {PORT}")
    print("=" * 64)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
