# Argentura Greenfield Baileys Worker - Luna-Bot Integration

This module provides the production bridge connecting Decaflex's Argentura platform (Baileys WhatsApp worker) to the Luna-Bot AI companion engine.

## 1. Architecture Overview

```
Incoming Customer WhatsApp Message
             │
             ▼
┌───────────────────────────────────────────────┐
│  Baileys Greenfield Worker                    │
│  (argentura-greenfield-worker.service)        │
│  File: worker/durable-job-worker.js           │
└──────────────────────┬────────────────────────┘
                       │
             Target Provider / Cutover Check
             (Agency Diana1 / Lady Lila)
                       │
        ┌──────────────┴──────────────┐
        │                             │
    [Yes (Lila / Diana1)]       [No (Other Agencies)]
        │                             │
        ▼                             ▼
┌─────────────────────────┐   ┌─────────────────────────┐
│ worker/luna-bridge.js   │   │ Standard Auto-Responder │
│ Intercepts Job & calls  │   │ (createSocialSmalltalk, │
│ http://127.0.0.1:8000/  │   │  time selection, etc.)  │
│ whatsapp/webhook        │   └─────────────────────────┘
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│ Luna-Bot Engine         │
│ - Grok-4.3 Model        │
│ - Strict Stealth Persona│
│ - 0 AI Disclosures      │
│ - German Texting Style  │
└───────────┬─────────────┘
            │ Returns AgentResultV2
            ▼
┌───────────────────────────────────────────────┐
│ Transactional PostgreSQL Outbox & Delivery    │
│ (Old responder bypassed; zero duplicate msgs) │
└───────────────────────────────────────────────┘
```

## 2. Key Components

- **`luna-bridge.js`**:
  - Implemented in `/opt/argentura/releases/current/worker/luna-bridge.js`.
  - Exported function: `resolveLunaReply(job, completedAt)`.
  - Filters incoming `ConversationJobV2` jobs for `ladyName: Lila` or `agencyId: Diana1` or `LUNA_CUTOVER=true`.
  - Calls `http://127.0.0.1:8000/whatsapp/webhook` with `dispatch: false`.
  - Constructs a strictly compliant `AGENT_RESULT_V2` conforming to `contracts/conversation-agent-v2.js` and `contracts/conversation-agent-dispatch.js`.
  - Passes all correlation and lease assertions (`jobId`, `correlationId`, `idempotencyKey`, `agencyId`, `ladyId`, `customerId`, `conversationId`, `expectedConversationVersion`).

- **Mutual Exclusion Guarantee**:
  - In `worker/durable-job-worker.js`:
    ```javascript
    const lunaBotResult = await lunaBridge.resolveLunaReply(executionJob, now());
    const deterministicResult = lunaBotResult || (modelLed ? null : ...);
    ```
  - When `lunaBotResult` is returned, the 13 built-in rule responders are completely bypassed.
  - The worker settles the result deterministically and queues outbound transmission natively through Baileys.
  - For non-target agencies/providers, `lunaBotResult` returns `null`, leaving existing behavior 100% untouched.

- **Non-Dispatch Webhook Mode (`app/api/whatsapp.py`)**:
  - `IncomingWhatsAppMessage` accepts `dispatch: bool = False`.
  - When called from the Baileys worker bridge, Luna-Bot generates the AI reply, persists conversation turns in Redis (`redis://127.0.0.1:6379/0`), and returns the text payload directly without making a redundant outbound HTTP request.
