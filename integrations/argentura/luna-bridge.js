// Luna-Bot Integration Bridge for Argentura Greenfield Baileys Worker
"use strict";

const http = require("http");
const fs = require("fs");
const crypto = require("node:crypto");
const { AGENT_RESULT_V2, createFailedAgentResultV2 } = require("../contracts/conversation-agent-v2");

const LOG_FILE = "/opt/argentura/releases/current/worker/luna-bridge.log";

function logBridge(msg) {
  try {
    fs.appendFileSync(LOG_FILE, `[${new Date().toISOString()}] ${msg}\n`);
  } catch (e) {
    // Ignore logging errors
  }
}

const pendingBookingIntents = new Map();
let stateCipher = null;

function setCipher(cipher) {
  stateCipher = cipher;
}

function getCipher() {
  return stateCipher;
}

function storePendingBookingIntent(jobId, intent) {
  if (jobId && intent) {
    pendingBookingIntents.set(String(jobId), intent);
  }
}

function consumePendingBookingIntent(jobId) {
  if (!jobId) return null;
  const key = String(jobId);
  const intent = pendingBookingIntents.get(key) || null;
  if (intent) {
    pendingBookingIntents.delete(key);
  }
  return intent;
}

function hasPendingBookingIntent(jobId) {
  return jobId ? pendingBookingIntents.has(String(jobId)) : false;
}

/**
 * Call Luna-Bot Webhook (FastAPI on 127.0.0.1:8000)
 */
function callLunaBotWebhook(payload) {
  return new Promise((resolve) => {
    const data = JSON.stringify(payload);
    logBridge(`[callLunaBotWebhook] Request: ${data}`);
    const req = http.request({
      hostname: "127.0.0.1",
      port: 8000,
      path: "/whatsapp/webhook",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(data)
      },
      timeout: 28000
    }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", chunk => { body += chunk; });
      res.on("end", () => {
        try {
          const parsed = JSON.parse(body);
          logBridge(`[callLunaBotWebhook] Response (status ${res.statusCode}): ${body}`);
          resolve(parsed);
        } catch (e) {
          logBridge(`[callLunaBotWebhook] JSON parse error: ${e.message}, body: ${body}`);
          resolve(null);
        }
      });
    });

    req.on("error", (err) => {
      logBridge(`[callLunaBotWebhook] Request error: ${err.message}`);
      resolve(null);
    });
    req.on("timeout", () => {
      logBridge(`[callLunaBotWebhook] Request timed out`);
      req.destroy();
      resolve(null);
    });
    req.write(data);
    req.end();
  });
}

/**
 * Resolve whether Luna-Bot should handle this conversation turn.
 * If yes, calls Luna-Bot and returns a compliant AgentResultV2.
 * All ConversationJobV2 turns are routed to Luna-Bot.
 */
async function resolveLunaReply(job, completedAt) {
  try {
    if (!job || job.schemaVersion !== "ConversationJobV2") {
      logBridge(`[resolveLunaReply] Skipped: job is not ConversationJobV2 (schema=${job?.schemaVersion})`);
      return null;
    }

    const ladyName = String(job.ladyContext?.displayName || job.conversationContext?.ladyName || "").trim();
    const ladyId = String(job.ladyId || "").trim();
    const agencyId = String(job.agencyId || "").trim();

    logBridge(`[resolveLunaReply] Routing to Luna-Bot: ladyName="${ladyName}" ladyId="${ladyId}" agencyId="${agencyId}"`);

    let customerText = "";
    if (Array.isArray(job.conversationContext?.recentMessages)) {
      const msgs = job.conversationContext.recentMessages.slice().reverse();
      for (const m of msgs) {
        if (!m) continue;
        const sender = String(m.sender || m.role || m.author || "").toLowerCase();
        if (sender === "customer" || sender === "user" || sender === "client") {
          customerText = m.text || m.content || m.body || m.message || "";
          if (customerText) break;
        }
      }
    }
    if (!customerText) {
      customerText = job.customerMessageText || job.currentMessageText || job.customerMessage || job.message || "";
    }

    logBridge(`[resolveLunaReply] customerText="${customerText}"`);
    if (!customerText || typeof customerText !== "string" || !customerText.trim()) {
      logBridge(`[resolveLunaReply] Failed: empty customer text`);
      return createFailedAgentResultV2(job, "LUNA_EMPTY_CUSTOMER_TEXT", completedAt);
    }

    const customerPhone = String(job.customerPhone || job.conversationContext?.customerPhone || job.customerId || "unknown");
    const customerName = String(job.customerName || job.conversationContext?.customerName || "Customer");

    const ladyContext = {
      ...(job.ladyContext || {}),
      allowedVisitTypes: job.bookingContext?.allowedVisitTypes || job.ladyContext?.allowedVisitTypes || ['incall'],
      travelFeeRule: job.bookingContext?.travelPlanning?.feeRule || job.ladyContext?.travelFeeRule || null,
    };

    const response = await callLunaBotWebhook({
      lady_id: ladyId || undefined,
      lady_name: ladyName || undefined,
      agency_id: agencyId || undefined,
      lady_context: ladyContext,
      availability_context: job.availabilityContext || null,
      booking_context: job.bookingContext || null,
      response_language: job.conversationContext?.responseLanguage || null,
      conversation_state: job.conversationContext?.conversationState || "AI_ACTIVE",
      ownership_kind: job.conversationContext?.ownershipKind || "ai",
      chat_id: String(job.conversationId || "default"),
      sender_phone: customerPhone,
      sender_name: customerName,
      message: customerText,
      dispatch: false
    });

    if (!response || !response.reply) {
      logBridge(`[resolveLunaReply] Luna silent or unavailable`);
      return createFailedAgentResultV2(job, "LUNA_SILENT_OR_UNAVAILABLE", completedAt);
    }

    logBridge(`[resolveLunaReply] Success! Luna reply length: ${response.reply.length}`);
    if (response.booking_intent) {
      const targetJobId = job.jobId || job.id;
      logBridge(`[resolveLunaReply] Storing pending booking_intent for jobId=${targetJobId}: ${JSON.stringify(response.booking_intent)}`);
      storePendingBookingIntent(targetJobId, response.booking_intent);
    }

    return Object.freeze({
      schemaVersion: AGENT_RESULT_V2,
      jobId: job.jobId,
      correlationId: job.correlationId,
      idempotencyKey: job.idempotencyKey,
      agencyId: job.agencyId,
      ladyId: job.ladyId,
      customerId: job.customerId,
      conversationId: job.conversationId,
      expectedConversationVersion: job.expectedConversationVersion,
      outcome: "reply",
      replyText: response.reply,
      toolIntents: Object.freeze([]),
      bookingDraftSuggestion: null,
      leadScoreSuggestion: null,
      memorySuggestions: Object.freeze([]),
      conversationSummarySuggestion: null,
      errorCode: null,
      completedAt: completedAt || new Date().toISOString()
    });
  } catch (err) {
    logBridge(`[resolveLunaReply] Error: ${err.message}\n${err.stack}`);
    return createFailedAgentResultV2(job, "LUNA_BRIDGE_ERROR", completedAt);
  }
}

function getEffectiveCipher(override) {
  if (override && typeof override.encrypt === "function") return override;
  if (stateCipher && typeof stateCipher.encrypt === "function") return stateCipher;
  try {
    const key = process.env.ARGENTURA_WHATSAPP_STATE_KEY || Buffer.alloc(32, 7).toString("base64");
    let createWhatsAppStateCipher;
    try {
      createWhatsAppStateCipher = require("./postgres-greenfield-whatsapp-runtime").createWhatsAppStateCipher;
    } catch {
      createWhatsAppStateCipher = require("../runtime/postgres-greenfield-whatsapp-runtime").createWhatsAppStateCipher;
    }
    return createWhatsAppStateCipher({ key });
  } catch (e) {
    return null;
  }
}

async function txOneOrNone(tx, sql, params) {
  if (typeof tx.oneOrNone === "function") {
    return await tx.oneOrNone(sql, params);
  }
  if (typeof tx.one === "function") {
    try {
      return await tx.one(sql, params);
    } catch (e) {
      return null;
    }
  }
  if (typeof tx.query === "function") {
    const res = await tx.query(sql, params);
    const rows = Array.isArray(res) ? res : res?.rows;
    return rows && rows.length > 0 ? rows[0] : null;
  }
  return null;
}

/**
 * Persist Luna-Bot appointment intent into PostgreSQL within the claimed job's tenant transaction.
 * Integrates directly with Argentura's automated AI booking workflow.
 */
async function persistBookingInTransaction(transaction, executionJob, bookingIntent, cipherOverride = null) {
  const cipher = getEffectiveCipher(cipherOverride);
  const agencyId = String(executionJob.agencyId || "").trim();
  const ladyId = String(executionJob.ladyId || "").trim();
  const customerId = String(executionJob.customerId || "").trim();
  const conversationId = String(executionJob.conversationId || "").trim();
  const targetJobId = executionJob.jobId || executionJob.id || null;

  logBridge(`[persistBookingInTransaction] Starting automated booking persistence for agencyId="${agencyId}", ladyId="${ladyId}", customerId="${customerId}", targetJobId="${targetJobId}"`);

  // 1. Resolve durable_jobs fence metadata and channelIdentityId
  let jobRow = null;
  let channelIdentityId = null;
  if (targetJobId) {
    try {
      jobRow = await txOneOrNone(
        transaction,
        `SELECT id, channel_identity_id, agent_activation_revision_id, agent_lifecycle_generation,
                agent_execution_mode, expected_conversation_version, conversation_id, lady_id, customer_id
           FROM durable_jobs
          WHERE agency_id = $1 AND id = $2`,
        [agencyId, targetJobId]
      );
      if (jobRow?.channel_identity_id) {
        channelIdentityId = jobRow.channel_identity_id;
      }
    } catch (e) {
      logBridge(`[persistBookingInTransaction] durable_jobs lookup notice: ${e.message}`);
    }
  }

  if (!channelIdentityId) {
    try {
      const fallbackChannel = await txOneOrNone(
        transaction,
        `SELECT id FROM customer_channel_identities WHERE agency_id = $1 AND customer_id = $2 AND lady_id = $3 LIMIT 1`,
        [agencyId, customerId, ladyId]
      );
      channelIdentityId = fallbackChannel?.id || null;
    } catch (e) {
      logBridge(`[persistBookingInTransaction] channel_identity_id lookup from customer_channel_identities notice: ${e.message}`);
    }
  }

  // 2. Resolve Lady Timezone
  let timeZone = "Europe/Berlin";
  try {
    const ladyRow = await txOneOrNone(
      transaction,
      `SELECT time_zone FROM girls WHERE agency_id = $1 AND id = $2`,
      [agencyId, ladyId]
    );
    if (ladyRow?.time_zone) {
      timeZone = ladyRow.time_zone;
    }
  } catch (e) {
    // default to Europe/Berlin
  }

  // 3. Compute timestamps
  const dateKey = String(bookingIntent.date || "").trim();
  const startTime = String(bookingIntent.start_time || "15:00").trim();
  let endTime = bookingIntent.end_time ? String(bookingIntent.end_time).trim() : null;
  const durationMinutes = Number(bookingIntent.duration_minutes || 60);

  if (!endTime) {
    const [h, m] = startTime.split(":").map(Number);
    const endMinutes = (h || 0) * 60 + (m || 0) + durationMinutes;
    const endH = Math.floor(endMinutes / 60) % 24;
    const endM = endMinutes % 60;
    endTime = `${String(endH).padStart(2, "0")}:${String(endM).padStart(2, "0")}`;
  }

  const { localWindowToInterval } = require("../postgres/booking-time");
  const interval = localWindowToInterval(dateKey, startTime, endTime, timeZone);
  const startsAt = interval.startsAt;
  const endsAt = interval.endsAt;

  // 4. Resolve customer details (Name & Phone) from PostgreSQL customers database
  let resolvedCustomerName = (bookingIntent.customer_name && bookingIntent.customer_name !== "Kunde") ? bookingIntent.customer_name : null;
  let resolvedCustomerPhone = bookingIntent.customer_phone || null;

  try {
    const custRow = await txOneOrNone(
      transaction,
      `SELECT display_name, external_key FROM customers WHERE agency_id = $1 AND id = $2`,
      [agencyId, customerId]
    );
    if (custRow) {
      if (!resolvedCustomerName && custRow.display_name && custRow.display_name.trim() && custRow.display_name.trim() !== "Kunde") {
        resolvedCustomerName = custRow.display_name.trim();
      }
      if (!resolvedCustomerPhone && custRow.external_key) {
        let phone = custRow.external_key.split('@')[0].trim();
        if (phone.length >= 6) {
          if (!phone.startsWith('+') && /^\d+$/.test(phone)) {
            phone = '+' + phone;
          }
          resolvedCustomerPhone = phone;
        }
      }
    }
  } catch (e) {
    logBridge(`[persistBookingInTransaction] customer lookup notice: ${e.message}`);
  }

  if (!resolvedCustomerPhone && channelIdentityId) {
    try {
      const channelRow = await txOneOrNone(
        transaction,
        `SELECT external_key FROM customer_channel_identities WHERE agency_id = $1 AND id = $2`,
        [agencyId, channelIdentityId]
      );
      if (channelRow?.external_key) {
        let phone = channelRow.external_key.split('@')[0].trim();
        if (phone.length >= 6) {
          if (!phone.startsWith('+') && /^\d+$/.test(phone)) {
            phone = '+' + phone;
          }
          resolvedCustomerPhone = phone;
        }
      }
    } catch (e) {
      logBridge(`[persistBookingInTransaction] channel identity lookup notice: ${e.message}`);
    }
  }

  const customerName = String(resolvedCustomerName || executionJob.customerName || "Kunde").trim();
  const customerPhone = String(resolvedCustomerPhone || executionJob.customerPhone || "").trim();

  // 5. Resolve active service from lady_services
  let serviceId = null;
  let serviceName = durationMinutes ? `${durationMinutes} Min` : "Standard";
  try {
    const serviceRows = await transaction.query(
      `SELECT id, name, duration_minutes, amount_minor
         FROM lady_services
        WHERE agency_id = $1 AND lady_id = $2 AND active = TRUE
        ORDER BY sort_order ASC, id ASC`,
      [agencyId, ladyId]
    );
    const rows = Array.isArray(serviceRows) ? serviceRows : serviceRows?.rows;
    if (rows && rows.length > 0) {
      const matched = rows.find(s => Number(s.duration_minutes) === Number(durationMinutes));
      if (matched) {
        serviceId = matched.id;
        serviceName = matched.name || serviceName;
      } else {
        serviceId = rows[0].id;
        serviceName = rows[0].name || serviceName;
      }
    }
  } catch (e) {
    logBridge(`[persistBookingInTransaction] lady_services lookup notice: ${e.message}`);
  }
  if (!serviceId) {
    serviceId = `profile-duration-${durationMinutes || 60}`;
  }

  // 6. Generate canonical IDs
  const rawUuid = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : crypto.randomBytes(16).toString("hex");
  const requestId = `req-${rawUuid}`;
  const publicAppointmentId = `appointment-public-${rawUuid}`;

  // 7. Encrypt customer details (PII)
  const cipherContextBase = `freeze-manual-calendar:${publicAppointmentId}`;
  let customerNameCiphertext = null;
  let customerPhoneCiphertext = null;
  let notesCiphertext = null;

  if (cipher && typeof cipher.encrypt === "function") {
    try {
      customerNameCiphertext = cipher.encrypt(customerName, `${cipherContextBase}:customer-name`);
      if (customerPhone) {
        customerPhoneCiphertext = cipher.encrypt(customerPhone, `${cipherContextBase}:phone`);
      }
      if (bookingIntent.notes) {
        notesCiphertext = cipher.encrypt(String(bookingIntent.notes), `${cipherContextBase}:notes`);
      }
    } catch (encErr) {
      logBridge(`[persistBookingInTransaction] PII encryption failed: ${encErr.message}`);
    }
  }

  const amountMinor = Math.round(Number(bookingIntent.price_eur || 60) * 100);
  const visitType = (bookingIntent.location_type || "incall").toLowerCase() === "outcall" ? "outcall" : "incall";
  const locationText = visitType === "outcall" ? String(bookingIntent.customer_address || "Outcall").trim() : null;
  const requestFingerprint = crypto.createHash("sha256").update(`${agencyId}:${ladyId}:${requestId}:${startsAt}`).digest("hex");
  const idempotencyKey = `agent:${targetJobId || requestId}:0`;

  // 8. Update customer display_name if empty or generic
  if (customerName && customerName !== "Kunde") {
    try {
      await transaction.query(
        `UPDATE customers SET display_name = $1 WHERE agency_id = $2 AND id = $3 AND (display_name IS NULL OR display_name = '' OR display_name = 'Kunde')`,
        [customerName, agencyId, customerId]
      );
    } catch (e) {
      logBridge(`[persistBookingInTransaction] update customer display_name notice: ${e.message}`);
    }
  }

  // 9. Insert automated appointment_requests
  await transaction.query(
    `INSERT INTO appointment_requests (
       agency_id, id, lady_id, customer_id, channel_identity_id, conversation_id,
       source_appointment_id, request_kind, state, version, expected_conversation_version,
       service_id, service_name_snapshot, amount_minor, currency_code, visit_type,
       requested_starts_at, requested_ends_at, time_zone, customer_note,
       request_fingerprint, idempotency_key, created_by_type, created_by_id,
       source_conversation_job_id, agent_activation_revision_id,
       agent_conversation_version, agent_lifecycle_generation,
       agent_execution_mode, agent_delivery_allowed,
       appointment_location_text
     ) VALUES (
       $1, $2, $3, $4, $5, $6,
       NULL, 'booking', 'waiting_for_lady', 1, $7::bigint,
       $8, $9, $10::bigint, 'EUR', $11,
       $12::timestamptz, $13::timestamptz, $14, $15,
       $16, $17, 'agent', $18,
       $19, $20,
       $21::bigint, $22::bigint,
       $23, TRUE,
       $24
     ) ON CONFLICT (agency_id, id) DO NOTHING`,
    [
      agencyId, requestId, ladyId, customerId, channelIdentityId, conversationId || null,
      jobRow?.expected_conversation_version || executionJob.expectedConversationVersion || 1,
      serviceId, serviceName, amountMinor, visitType,
      startsAt, endsAt, timeZone, bookingIntent.notes || null,
      requestFingerprint, idempotencyKey, targetJobId || 'luna-bot',
      jobRow?.id || targetJobId || null,
      jobRow?.agent_activation_revision_id || null,
      jobRow?.expected_conversation_version ? (BigInt(jobRow.expected_conversation_version) + 1n).toString() : '2',
      jobRow?.agent_lifecycle_generation || null,
      jobRow?.agent_execution_mode || 'external_pilot',
      locationText
    ]
  );

  // 10. Insert appointment_public_id_bridges
  await transaction.query(
    `INSERT INTO appointment_public_id_bridges (
       agency_id, request_id, public_appointment_id, lady_id, customer_id
     ) VALUES ($1, $2, $3, $4, $5)
     ON CONFLICT DO NOTHING`,
    [agencyId, requestId, publicAppointmentId, ladyId, customerId]
  );

  // 11. Insert appointment_private_details
  if (customerNameCiphertext) {
    await transaction.query(
      `INSERT INTO appointment_private_details (
         agency_id, request_id, customer_name_ciphertext, customer_phone_ciphertext,
         notes_ciphertext, service_type, price_supplied, forced_work_hours,
         cipher_context
       ) VALUES ($1, $2, $3, $4, $5, $6, TRUE, FALSE, $7)
       ON CONFLICT (agency_id, request_id) DO NOTHING`,
      [
        agencyId, requestId, customerNameCiphertext, customerPhoneCiphertext,
        notesCiphertext, serviceName, cipherContextBase
      ]
    );
  }

  // 12. Insert appointment_request_status_history
  await transaction.query(
    `INSERT INTO appointment_request_status_history (
       agency_id, request_id, from_state, to_state, version, actor_type, actor_id, reason
     ) VALUES ($1, $2, NULL, 'waiting_for_lady', 1, 'agent', $3, 'Luna-Bot booking via WhatsApp')
     ON CONFLICT (agency_id, request_id, version) DO NOTHING`,
    [agencyId, requestId, targetJobId || 'luna-bot']
  );

  // 13. Trigger response cycle
  try {
    await transaction.query(
      `SELECT public.argentura_start_appointment_response_cycle($1, $2)`,
      [agencyId, requestId]
    );
  } catch (err) {
    logBridge(`[persistBookingInTransaction] response cycle notice: ${err.message}`);
  }

  logBridge(`[persistBookingInTransaction] SUCCESS! Automated booking requestId=${requestId}, publicAppointmentId=${publicAppointmentId}, date=${dateKey}, startsAt=${startsAt}, customer=${customerName}, phone=${customerPhone}`);

  return Object.freeze({
    requestId,
    publicAppointmentId,
    startsAt,
    endsAt
  });
}

module.exports = {
  resolveLunaReply,
  callLunaBotWebhook,
  logBridge,
  storePendingBookingIntent,
  consumePendingBookingIntent,
  hasPendingBookingIntent,
  setCipher,
  getCipher,
  persistBookingInTransaction
};
