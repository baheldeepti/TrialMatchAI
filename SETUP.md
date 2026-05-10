# SETUP — Beginner walkthrough for macOS

> This guide assumes you have **never used Terminal before**. Every command and every click is spelled out. Total time: **30-45 minutes**.

---

## Phase 0 — Open Terminal & some Mac basics

1. Press `⌘ + Space` to open **Spotlight Search**.
2. Type `Terminal` and press `Return`. A black or white window opens — that's the Terminal.
3. Pin it to your Dock (right-click the Terminal icon in the Dock → Options → Keep in Dock) — you'll be using it a lot.

A few commands you'll see in this guide:

| Command | What it does |
|---------|--------------|
| `cd <folder>` | Change into a folder |
| `ls` | List files in the current folder |
| `pwd` | Show which folder you're currently in |
| `python3 ...` | Run Python |

When the guide says **"run this command"**, it means: copy the line, paste it into Terminal (`⌘ + V`), and press `Return`.

---

## Phase 1 — Sign up for Prompt Opinion & connect a model (~5 min)

1. Go to **https://app.promptopinion.ai** in your browser.
2. Click **Sign up**. Fill in your details, click **Create**.
3. You'll land in the workspace. The first thing it asks for is a model.
4. Open a second tab → **https://aistudio.google.com**. Sign in with a Gmail account.
5. In Google AI Studio, click **Get API key** → **Create API key** → **Create new key in new project**. Copy the key (starts with `AIza...`).
6. Back in Prompt Opinion: paste the API key, click **Load models**, choose **Gemini 3.1 Flash Lite**, give it a nickname (e.g., "Gemini Flash"), click **Add model**.

✅ You now have an active model in your workspace.

---

## Phase 2 — Add patients & upload clinical notes (~5 min)

1. In Prompt Opinion, go to the **Patients** tab.
2. The simplest path: click **Add patient** → fill in basic demographics for our first test patient.

   **Maria Rodriguez** — DOB 1973-04-15, Female, San Francisco CA, *condition: HER2+ metastatic breast cancer*

3. Open her patient record → **Documents** → **Upload**. Upload `patients/maria_rodriguez_clinical_note.md` (or drag-and-drop).
4. Repeat for **James Chen** (DOB 1958-11-22, Male, Boston MA — diabetes/heart failure) and **Sarah Kumar** (DOB 1991-07-08, Female, Chicago IL — rheumatoid arthritis), uploading their respective notes.

> **Tip:** the platform also has a sample-patient importer. Either approach works — what matters is that the clinical notes are uploaded so the agent can read them.

✅ Three patients ready, each with a rich clinical note.

---

## Phase 3 — Get the code onto your Mac (~5 min)

1. In Terminal, run:
   ```bash
   cd ~/Desktop
   ```
   *(this puts us on your Desktop so the project is easy to find)*

2. The folder `trialmatch-ai/` was generated for you — copy it to your Desktop. If you downloaded a zip, double-click to extract, then drag the `trialmatch-ai` folder onto your Desktop.

3. Move into the project folder:
   ```bash
   cd ~/Desktop/trialmatch-ai
   ls
   ```
   You should see `trialmatch_mcp.py`, `requirements.txt`, `README.md`, etc.

---

## Phase 4 — Install Python dependencies (~5 min)

macOS already has Python 3 installed. We'll create an isolated environment so we don't pollute your system.

1. From inside `~/Desktop/trialmatch-ai`, run:
   ```bash
   python3 -m venv .venv
   ```
   *(creates an isolated environment named `.venv`)*

2. Activate it:
   ```bash
   source .venv/bin/activate
   ```
   Your prompt should now start with `(.venv)`.

3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   *(if it complains about pip being out of date, run `pip install --upgrade pip` first)*

✅ Python environment ready.

---

## Phase 5 — Run the MCP server (~2 min)

Still in the activated `.venv`, run:

```bash
python trialmatch_mcp.py
```

You should see:
```
================================================================
🚀 TrialMatch & Triage — MCP Server
================================================================
  Listening on:  http://0.0.0.0:8000/mcp
  Health check:  http://0.0.0.0:8000/health
  Tools:
    • read_patient_fhir
    • search_clinical_trials
    • match_trials_to_patient
    • extract_eligibility_signals
    • triage_patient_urgency
    • search_pubmed_research
  FHIR extension: ai.promptopinion/fhir-context  ✅
  Expose with:   ngrok http 8000
================================================================
```

You can also test it from another terminal:
```bash
curl http://localhost:8000/health
```
This should return JSON with `"ok": true` and the list of tools.

**Leave this Terminal window running.** That's your MCP server.

---

## Phase 6 — Expose it with ngrok (~5 min)

ngrok creates a public HTTPS URL that points to your local server, so Prompt Opinion can reach it.

1. Open a **new Terminal window** (`⌘ + N` from Terminal). The first window keeps running the server.
2. Go to **https://ngrok.com** → sign up free → on the dashboard, you'll see a "Setup & Installation" section with a download for macOS.
3. Easiest install: if you have Homebrew (`brew --version` returns a version), run:
   ```bash
   brew install ngrok
   ```
   Otherwise, download the macOS .zip from ngrok's site, double-click to extract, and move the `ngrok` binary to `/usr/local/bin` (you can drag-drop it there in Finder; you'll be asked for your password).

4. From the ngrok dashboard, copy your **Authtoken**, then run (in the new Terminal):
   ```bash
   ngrok config add-authtoken YOUR_TOKEN_HERE
   ```

5. Start the tunnel:
   ```bash
   ngrok http 8000
   ```

   ngrok prints something like:
   ```
   Forwarding    https://abc123-de45-67ef-89ab.ngrok-free.app -> http://localhost:8000
   ```

6. **Copy the `https://...ngrok-free.app` URL** — you'll paste it into Prompt Opinion next.

> Keep BOTH terminal windows open: window 1 = your MCP server, window 2 = ngrok.

---

## Phase 7 — Register the MCP in Prompt Opinion (~3 min)

1. In Prompt Opinion, go to **Workspace Hub** (left sidebar) → **MCP Servers**.
2. **If you've added this server before**, click the existing entry and either delete it (then add fresh) or edit and update its URL — Prompt Opinion deduplicates by name and endpoint, so you can't add a duplicate.
3. Click **Add MCP server**. Fill in:
   - **Name:** `TrialMatch & Triage MCP`
   - **URL:** paste your ngrok URL **and append `/mcp`** at the end. Example: `https://abc123-de45-67ef-89ab.ngrok-free.app/mcp`
   - **Transport:** Streamable HTTP
   - **Authentication:** None (we'll keep it simple for the demo)
4. Click **Continue** (or **Test**). Prompt Opinion sends an `initialize` request to your server. Because we declared the `ai.promptopinion/fhir-context` extension, you should now see:
   - ✅ A trust toggle for the FHIR extension — turn it ON
   - ✅ A list of FHIR scopes the server requests (Patient, Condition, Observation, MedicationRequest, AllergyIntolerance) — leave them all checked
   - ✅ All 6 tools listed: `read_patient_fhir`, `search_clinical_trials`, `match_trials_to_patient`, `extract_eligibility_signals`, `triage_patient_urgency`, `search_pubmed_research`
5. Click **Save**.

✅ Your tools are now discoverable inside Prompt Opinion, and the workspace will pass FHIR context (URL, token, patient ID) to your server on every tool call.

---

## Phase 8 — Build the BYO agent (~5 min)

1. Go to **Agents** → **Build your own agents** → **Configure new agent**.
2. Open `agent_config/skill_definition.md` and copy each value into the matching field:
   - **Name:** `TrialMatch AI`
   - **Description:** *(use the description from skill_definition.md)*
   - **Context type:** `Patient`
   - **System prompt:** paste the **entire** contents of `agent_config/system_prompt.md`
   - **A2A:** ✅ ON
   - **FHIR context:** ✅ ON
   - **Skill:** name = `match_patient_to_clinical_trials`, description from skill_definition.md
3. **Tools:** click **Add tool** → select all six tools from your `TrialMatch & Triage MCP` server (`read_patient_fhir`, `search_clinical_trials`, `match_trials_to_patient`, `extract_eligibility_signals`, `triage_patient_urgency`, `search_pubmed_research`).
4. **Save.**

✅ Agent ready.

---

## Phase 9 — Test it (~5 min)

1. Go to **Launchpad**.
2. Select **Maria Rodriguez** as the active patient.
3. Select the **General Chat** agent (this is the one the judges will use; it will consult your TrialMatch agent over A2A).
4. Try this prompt:

   > Use TrialMatch AI to find clinical trials that might be a good fit for this patient. Focus on her HER2-positive metastatic breast cancer. She's based in San Francisco.

5. Watch the General Chat agent call **TrialMatch AI**, which then calls your MCP tools (`search_clinical_trials` → `rank_trials_for_patient` → `get_trial_eligibility`). Toggle the **Show tool calls** switch in the chat UI to see this happening live.

If you see a structured response with NCT IDs and chart-grounded ✓ / ⚠️ checklists — **it works**. 🎉

For more demo prompts and a video script, see `agent_config/test_prompts.md`.

---

## Phase 10 — Publish to the Marketplace (when ready to submit)

1. In Prompt Opinion, go to **Marketplace Studio** (left sidebar).
2. Choose **Submit an Agent**.
3. Fill in:
   - **Agent name:** TrialMatch AI
   - **Tagline:** "Connect patients to the right clinical studies."
   - **Description:** *(use the description from skill_definition.md)*
   - **Skill list:** match_patient_to_clinical_trials
   - **Required MCP servers:** TrialMatch & Triage MCP (or paste your ngrok URL)
4. Submit. Per the hackathon instructions this step makes your work testable by the judges.

---

## Phase 11 — Record your demo video

Open `agent_config/test_prompts.md`. Follow the **3-minute video script** in that file — it covers the hook, the live workflow, the eligibility "wow" moment, generalization to a second patient, and the close.

Tools that work well on Mac:
- **QuickTime Player → File → New Screen Recording** (free, built-in)
- **Loom** (free tier; auto-trims)
- **OBS Studio** (free; advanced)

---

## Common gotchas

| Problem | Fix |
|---------|-----|
| `python3: command not found` | Install from **python.org/downloads/macos/** |
| `pip: command not found` after activating venv | Run `python -m pip install -r requirements.txt` instead |
| `ngrok: command not found` | Move the ngrok binary to `/usr/local/bin` or run with full path |
| ngrok URL changes every time | That's the free tier behavior. Re-paste the new URL into Prompt Opinion each session. Or use a paid static domain. |
| Tools list empty in Prompt Opinion | Confirm the URL ends in `/mcp`, ngrok is running, and the MCP server is still running in the other terminal |
| `ModuleNotFoundError: No module named 'starlette'` (or `httpx`) | You forgot to `source .venv/bin/activate` before running. |
| `Address already in use` | Port 8000 is taken. Kill the previous process: `lsof -ti:8000 \| xargs kill -9` |
| `An mcp server with this name or endpoint already exists in this workspace` | You already added this server before (often during testing). Go to Workspace Hub → MCP Servers → find the existing entry → edit (update URL) or delete and re-add. Prompt Opinion deduplicates by name and endpoint. |
| `This MCP server does not support PromptOpinion's FHIR extension` | Your server didn't return the `ai.promptopinion/fhir-context` extension on `initialize`. The server in this repo declares it correctly — make sure you're running `trialmatch_mcp.py` (not an older version) and that nothing is intercepting the request. Verify with: `curl http://localhost:8000/health` — should list `fhir_extension: ai.promptopinion/fhir-context`. |
| Tool calls work but FHIR data is empty | Confirm a patient is selected in the Launchpad and that you authorized the FHIR scopes when adding the MCP server. The headers `X-FHIR-Server-URL`, `X-FHIR-Access-Token`, `X-Patient-ID` need to all be present on tool calls. |

---

## What "good" looks like in your demo

- Tool calls fire in the right order without you scripting them in the prompt.
- Eligibility reasoning is **chart-grounded** — every ✓ traces back to a real datapoint in the patient note.
- Output is **patient-friendly** — your grandmother could understand it.
- The agent **flags uncertainty** instead of inventing answers.
- All NCT IDs are real and clickable.

You're ready. 🚀
