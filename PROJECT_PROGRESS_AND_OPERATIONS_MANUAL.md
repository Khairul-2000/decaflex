# Project Progress, Solved Issues & Operations Manual
**Project:** Luna-Bot & Argentura Greenfield Baileys Integration  
**Client / Partner:** Decaflex  
**Date:** October 2026  
**Status:** All Reported Issues Resolved & Live on Staging (`45.133.9.223`)

---

## Table of Contents
1. [Executive Summary & Architecture Overview](#1-executive-summary--architecture-overview)
2. [What We Have Done & Solved (Detailed Chronology)](#2-what-we-have-done--solved-detailed-chronology)
   - [Issue 1: Automated AI Booking Flow vs "Agenturtermin" & Alternative Time Proposals](#issue-1-automated-ai-booking-flow-vs-agenturtermin--alternative-time-proposals)
   - [Issue 2: Customer Name & Phone Number Display (Encrypted PII)](#issue-2-customer-name--phone-number-display-encrypted-pii)
   - [Issue 3: Escort (Outcall) Visits & Travel Fee Rules](#issue-3-escort-outcall-visits--travel-fee-rules)
   - [Issue 4: Address Formatting & Removal of "Studio" Terminology](#issue-4-address-formatting--removal-of-studio-terminology)
   - [Issue 5: German-First Language Consistency & Greetings](#issue-5-german-first-language-consistency--greetings)
   - [Issue 6: Out-of-Hours Messaging ("Feierabend" Guidance)](#issue-6-out-of-hours-messaging-feierabend-guidance)
   - [Issue 7: WhatsApp vs. Telegram Channel Readiness](#issue-7-whatsapp-vs-telegram-channel-readiness)
3. [The Operational Playbook: How We Handle Decaflex Feedback](#3-the-operational-playbook-how-we-handle-decaflex-feedback)
   - [Phase 1: Ingest & Triage Feedback](#phase-1-ingest--triage-feedback)
   - [Phase 2: Root Cause Investigation Across 2 Repositories & Server](#phase-2-root-cause-investigation-across-2-repositories--server)
   - [Phase 3: Local Implementation & Regression Protection](#phase-3-local-implementation--regression-protection)
   - [Phase 4: Automated Testing & Verification](#phase-4-automated-testing--verification)
   - [Phase 5: Commit & Push to Git Remotes](#phase-5-commit--push-to-git-remotes)
   - [Phase 6: Server Deployment & Service Restart](#phase-6-server-deployment--service-restart)
   - [Phase 7: Live Service & Log Verification](#phase-7-live-service--log-verification)
   - [Phase 8: Client & Codex Handover Communication](#phase-8-client--codex-handover-communication)
4. [Quick Reference Cheat Sheet](#4-quick-reference-cheat-sheet)

---

## 1. Executive Summary & Architecture Overview

The system operates across two distinct repositories and a live development server:

```
                      +---------------------------------------+
                      |       Customer WhatsApp Client        |
                      +---------------------------------------+
                                          |
                                          v
                      +---------------------------------------+
                      |        Baileys WhatsApp Socket        |
                      |   (Argentura Greenfield Worker)       |
                      +---------------------------------------+
                                          |
               +--------------------------+---------------------------+
               |                                                      |
               v                                                      v
  [Repository 1: argentura]                             [Repository 2: luna-bot]
  worker/luna-bridge.js                                 FastAPI Engine (:8000)
  - Intercepts ConversationJobV2                        - LLM Agent & System Prompts
  - Extracts Lady, Customer & Context                   - Geolocation & Pricing Tools
  - Persists AI booking to PostgreSQL                   - Stealth Booking Toolkit
  - Encrypts PII with AES-GCM                           - Few-Shot German Persona
               |                                                      |
               +--------------------------+---------------------------+
                                          |
                                          v
                      +---------------------------------------+
                      |        PostgreSQL 16 Database         |
                      |   - appointment_requests              |
                      |   - appointment_private_details       |
                      |   - durable_jobs                      |
                      +---------------------------------------+
                                          |
                                          v
                      +---------------------------------------+
                      |            Argentura Lady UI          |
                      |   Accept / Decline / Propose Time     |
                      +---------------------------------------+
```

### Components
1. **`luna-bot` (Python 3.12 / FastAPI / LangChain / Pydantic):**
   - Core AI conversational agent.
   - Handles persona steering, natural stealth booking without AI jargon, rate calculations, distance/travel fees, and out-of-hours responses.
2. **`argentura` (Node.js 24 / PostgreSQL 16 / Baileys):**
   - Background worker service (`argentura-greenfield-worker.service`).
   - Hosts `worker/luna-bridge.js`, which bridges Baileys conversation jobs to `luna-bot`, and persists confirmed booking intents directly into Argentura's database tables.

---

## 2. What We Have Done & Solved (Detailed Chronology)

### Issue 1: Automated AI Booking Flow vs "Agenturtermin" & Alternative Time Proposals
- **Problem:** When Luna created an appointment, the Lady UI showed the badge **"Agenturtermin"** (Agency appointment) instead of an AI request, and only offered **Accept** or **Decline**. The **"Alternative Zeit vorschlagen"** (Propose alternative time) button was missing, and the backend rejected attempts with `POSTGRES_BOOKING_MANUAL_ALTERNATIVE_FORBIDDEN`. Furthermore, when the Lady accepted the appointment, no WhatsApp confirmation was sent to the customer.
- **Root Cause:** In `worker/luna-bridge.js`, `persistBookingInTransaction()` inserted requests with `request_kind = 'manual'`, `created_by_type = 'tenant'`, and `created_by_id = 'luna-bot'`. Argentura's `ladyRequestOrigin()` mapped this combination to `agency_manual`, triggering manual safeguards that block alternative times and suppress outbox WhatsApp messages.
- **Solution:**
  - Rewrote request creation to use `request_kind = 'booking'`, `created_by_type = 'agent'`, and `created_by_id = targetJobId`.
  - Added full agent fencing: `source_conversation_job_id`, `agent_activation_revision_id`, `agent_lifecycle_generation`, `agent_execution_mode = 'external_pilot'`, and `agent_delivery_allowed = TRUE`.
  - Set status history with `actor_type = 'agent'`.
- **Outcome:** The Lady UI now classifies requests as automated AI bookings, displays the alternative-time proposal button, and automatically triggers the customer WhatsApp confirmation message via `enqueueOutbox` when accepted.

### Issue 2: Customer Name & Phone Number Display (Encrypted PII)
- **Problem:** Appointments in the calendar and Lady UI displayed generic `"Kunde"` and empty phone numbers, despite the customer being present in the database.
- **Root Cause:** The persistence function defaulted to generic fallbacks and did not query the `customers` or `customer_channel_identities` PostgreSQL tables, nor did it write encrypted records to `appointment_private_details`.
- **Solution:**
  - Added query lookups for `customers` (`display_name`, `external_key`) with fallback to `customer_channel_identities`.
  - Formatted phone numbers consistently with international prefix (`+49...`).
  - Encrypted customer name and phone into `customer_name_ciphertext` and `customer_phone_ciphertext` under context `freeze-manual-calendar:${publicAppointmentId}` using `ARGENTURA_WHATSAPP_STATE_KEY`.
- **Outcome:** Real customer names and clickable phone numbers are correctly displayed in the Lady UI appointment details modal.

### Issue 3: Escort (Outcall) Visits & Travel Fee Rules
- **Problem:** When customers requested escort/hotel visits, the bot stated it only accepted incall visits, even though the lady's profile had outcall enabled.
- **Root Cause:** `worker/luna-bridge.js` did not forward `bookingContext` to the webhook, and `luna-bot` defaulted to `outcall = False`.
- **Solution:**
  - Forwarded `bookingContext.allowedVisitTypes` and `bookingContext.travelPlanning.feeRule` from the worker job to the Luna webhook.
  - In `luna-bot/app/argentura/models.py`, `LadyProfile.from_lady_context()` parses `allowedVisitTypes` (`incall`, `outcall`) and `travelFeeRule` into `TravelFeeConfig`.
  - Updated `IncomingWhatsAppMessage` in `app/api/whatsapp.py` to accept and merge `booking_context`.
- **Outcome:** Outcall visits are fully recognized, checked via `check_outcall_distance`, and priced with travel fees when applicable.

### Issue 4: Address Formatting & Removal of "Studio" Terminology
- **Problem:** Escorts host at private apartments and strongly disliked the word "Studio". In addition, addresses had awkward commas (`"Berliner Str., 16"`).
- **Root Cause:** Address formatting joined street and house number with commas, and prompt instructions referenced "my studio".
- **Solution:**
  - In `app/argentura/models.py`, `studio_address` combines street and number cleanly: `"Berliner Str. 16, 10115, Berlin"`.
  - In `app/persona_builder.py` and `app/tools/booking_tools.py`, strictly replaced "Studio" with natural German phrasing: *"bei mir"* or *"an meiner Adresse"*.
  - Cleaned up schema descriptions in `booking_tools.py`.
- **Outcome:** Luna refers exclusively to private visits (*"bei mir"*), with zero mentions of "Studio".

### Issue 5: German-First Language Consistency & Greetings
- **Problem:** When customers greeted with a simple "Hi", Luna occasionally answered in English.
- **Root Cause:** Few-shot examples in `persona_builder.py` were written in English (`"Hey! How's your day going?"`).
- **Solution:**
  - Replaced few-shot examples with natural German: `"Hey, wie geht's dir?"`, `"Hi! Schön von dir zu hören."`.
  - Reinforced agency policy: Luna defaults strictly to German unless the customer explicitly writes in another language.
- **Outcome:** Greetings and casual dialogue reliably start and remain in natural German.

### Issue 6: Out-of-Hours Messaging ("Feierabend" Guidance)
- **Problem:** Inquiries sent late at night needed natural handling aligned with provider working hours.
- **Solution:**
  - Added explicit persona instructions for after-hours messages: casually explain that the lady has finished work for the day (*"habe schon Feierabend"* / *"liege schon im Bett"*), and warmly suggest meeting tomorrow during working hours.
- **Outcome:** Natural, authentic handling of late-night messages without breaking character.

### Issue 7: WhatsApp vs. Telegram Channel Readiness
- **Context:** Decaflex asked about WhatsApp and Telegram status.
- **Status:**
  - **WhatsApp:** 100% integrated via Argentura Baileys worker on `45.133.9.223`. No third-party API credentials needed.
  - **Telegram:** Luna-Bot already contains the native Telegram polling engine (`app/telegram/`). Ready to go live as soon as Decaflex provides their `TELEGRAM_BOT_TOKEN`.

---

## 3. The Operational Playbook: How We Handle Decaflex Feedback

When Decaflex or Codex provides feedback, errors, or testing logs, here is the exact 8-step methodology we follow:

### Phase 1: Ingest & Triage Feedback
1. **Analyze the report:** Identify whether the issue originates from:
   - **Conversation / Persona:** Prompting, wording, language, rates, tools (lives in `luna-bot`).
   - **Persistence / Worker:** PostgreSQL database, appointment status, PII encryption, job fencing (lives in `argentura/worker/luna-bridge.js`).
   - **Infrastructure / State:** Service crashes, port conflicts, database locks (lives on server `45.133.9.223`).
2. **Avoid Assumptions:** Verify against real source code before making changes.

### Phase 2: Root Cause Investigation Across 2 Repositories & Server
1. Check git status and current branch in both local repositories:
   - `luna-bot`: `feature/luna-bot-greenfield-integration`
   - `argentura`: `feature/luna-bot-baileys-integration`
2. Inspect the live server state via SSH:
   ```bash
   ssh mahmud@45.133.9.223 "systemctl --user status luna-bot --no-pager"
   ssh mahmud@45.133.9.223 "systemctl status argentura-greenfield-worker.service --no-pager"
   ```
3. Inspect system logs if an error occurred during testing:
   ```bash
   ssh mahmud@45.133.9.223 "journalctl --user -u luna-bot -n 50 --no-pager"
   ```

### Phase 3: Local Implementation & Regression Protection
1. Modify code in the appropriate repository:
   - Prompt/persona/tools: in `/home/khairul/Downloads/luna-bot`
   - Persistence/worker/encryption: in `/home/khairul/Downloads/argentura`
2. **Preserve existing workflows:** Ensure manual agency appointments created in the Argentura web UI remain completely unchanged.
3. Keep reference copies synchronized (`luna-bot/integrations/argentura/luna-bridge.js`).

### Phase 4: Automated Testing & Verification
1. In `luna-bot`: Run the complete test suite:
   ```bash
   .venv/bin/pytest tests/
   ```
   *(Must pass all 43 tests).*
2. In `argentura`: Run worker and persistence unit tests:
   ```bash
   node tests/luna-bridge-booking-persistence.test.js
   ```
   *(Must pass all tests).*

### Phase 5: Commit & Push to Git Remotes
1. In `luna-bot`:
   ```bash
   git add app/ tests/ integrations/
   git commit -m "fix: <clear description>"
   git push origin feature/luna-bot-greenfield-integration
   ```
2. In `argentura`:
   ```bash
   git add worker/ tests/
   git commit -m "fix(luna-bridge): <clear description>"
   git push origin feature/luna-bot-baileys-integration
   ```

### Phase 6: Server Deployment & Service Restart
1. Update `luna-bot` on the server:
   ```bash
   ssh mahmud@45.133.9.223 "cd /home/mahmud/luna-bot && git pull origin feature/luna-bot-greenfield-integration && systemctl --user restart luna-bot"
   ```
2. Update `worker/luna-bridge.js` on the server:
   ```bash
   scp /home/khairul/Downloads/argentura/worker/luna-bridge.js mahmud@45.133.9.223:/opt/argentura/releases/argentura-greenfield-5f745de04976a0ecaaf1ff4373aa641130b5ad36/worker/luna-bridge.js
   ```
3. Restart the background worker service:
   ```bash
   ssh mahmud@45.133.9.223 "sudo /usr/bin/systemctl restart argentura-greenfield-worker.service"
   ```

### Phase 7: Live Service & Log Verification
1. Verify both services are active:
   ```bash
   ssh mahmud@45.133.9.223 "systemctl --user status luna-bot --no-pager"
   ssh mahmud@45.133.9.223 "sleep 2 && systemctl status argentura-greenfield-worker.service --no-pager"
   ```
2. Verify Python unit tests on the server:
   ```bash
   ssh mahmud@45.133.9.223 "cd /home/mahmud/luna-bot && .venv/bin/pytest tests/"
   ```

### Phase 8: Client & Codex Handover Communication
1. Prepare a clear, professional technical summary.
2. Provide exact commit hashes and file references.
3. Clearly state the server deployment status so the client knows it is ready for immediate live testing.

---

## 4. Quick Reference Cheat Sheet

| Resource | Path / Value | Notes |
|---|---|---|
| **Server Host** | `45.133.9.223` (port 22) | User: `mahmud` |
| **Argentura Web UI** | `https://www.management-suite.com/argentura/` | Staging Web Portal |
| **Luna-Bot Service** | `systemctl --user status luna-bot` | Runs FastAPI on `127.0.0.1:8000` |
| **Argentura Worker** | `sudo /usr/bin/systemctl restart argentura-greenfield-worker.service` | Greenfield Baileys worker |
| **Luna-Bot Repo** | `https://github.com/Khairul-2000/decaflex` | Branch: `feature/luna-bot-greenfield-integration` |
| **Argentura Repo** | `https://github.com/DecaFlex/argentura` | Branch: `feature/luna-bot-baileys-integration` |
| **Server Luna Path** | `/home/mahmud/luna-bot` | Python venv: `/home/mahmud/luna-bot/.venv` |
| **Server Worker Path**| `/opt/argentura/releases/current/worker/luna-bridge.js` | Owned by `mahmud:argentura` |
