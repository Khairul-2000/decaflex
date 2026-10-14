// Luna-Bot Integration Bridge for Argentura Greenfield Baileys Worker
"use strict";

const http = require("http");
const { AGENT_RESULT_V2 } = require("../contracts/conversation-agent-v2");

/**
 * Call Luna-Bot Webhook (FastAPI on 127.0.0.1:8000)
 */
function callLunaBotWebhook(payload) {
  return new Promise((resolve) => {
    const data = JSON.stringify(payload);
    const req = http.request({
      hostname: "127.0.0.1",
      port: 8000,
      path: "/whatsapp/webhook",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(data)
      },
      timeout: 15000
    }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", chunk => { body += chunk; });
      res.on("end", () => {
        try {
          const parsed = JSON.parse(body);
          resolve(parsed);
        } catch {
          resolve(null);
        }
      });
    });

    req.on("error", () => resolve(null));
    req.on("timeout", () => {
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
 * If no (or on error), returns null so standard Argentura logic runs.
 */
async function resolveLunaReply(job, completedAt) {
  try {
    if (!job || job.schemaVersion !== "ConversationJobV2") return null;

    const ladyName = String(job.ladyContext?.displayName || job.conversationContext?.ladyName || "").trim();
    const ladyId = String(job.ladyId || "").trim();
    const agencyId = String(job.agencyId || "").trim();

    const isLila = ladyName.toLowerCase().includes("lila") || ladyId.toLowerCase().includes("lila");
    const isDiana = agencyId.toLowerCase().includes("diana");
    const forceCutover = process.env.LUNA_CUTOVER === "true" || (process.env.LUNA_TARGET_LADY && process.env.LUNA_TARGET_LADY.toLowerCase() === ladyName.toLowerCase());

    // Only intercept agreed cutover chats / target provider (Lila / Diana1)
    if (!isLila && !isDiana && !forceCutover) {
      return null;
    }

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
      customerText = job.currentMessageText || job.customerMessage || job.message || "";
    }

    if (!customerText || typeof customerText !== "string" || !customerText.trim()) {
      return null;
    }

    const customerPhone = String(job.customerPhone || job.conversationContext?.customerPhone || job.customerId || "unknown");
    const customerName = String(job.customerName || job.conversationContext?.customerName || "Customer");

    const response = await callLunaBotWebhook({
      lady_id: ladyId || undefined,
      chat_id: String(job.conversationId || "default"),
      sender_phone: customerPhone,
      sender_name: customerName,
      message: customerText,
      dispatch: false
    });

    if (!response || !response.reply) {
      return null;
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
    return null;
  }
}

module.exports = {
  resolveLunaReply,
  callLunaBotWebhook
};
