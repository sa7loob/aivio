// يحوّل رد OpenAI إلى: رسالة للزبون + حالة الطلب + التحويل لصاحب المحل.
// فشل OpenAI أو رد غير صالح => رسالة اعتذار قصيرة + تنبيه صاحب المحل (لا يبقى الزبون بلا رد).
// (وضع: مرة لكل item؛ المدخل رد OpenAI أو خطأه)
const crypto = require('crypto');
const req = $('Build request').item.json;
const FALLBACK = 'معليش، صار عندنا عطل بسيط في الرد الآلي. حد من المحل حيرد عليك في أقرب وقت 🌸';

function norm(s) {
  return String(s ?? '')
    .replace(/[ً-ْـ]/g, '')       // تشكيل وتطويل
    .replace(/[أإآ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي')
    .replace(/\s+/g, ' ').trim().toLowerCase();
}

function cleanText(v, max) {
  const s = String(v ?? '').trim();
  return s ? s.slice(0, max) : null;
}

function parse(resp) {
  if (!resp || resp.error) return null;
  const msg = resp.choices?.[0]?.message;
  if (!msg || msg.refusal || typeof msg.content !== 'string') return null;
  try {
    const data = JSON.parse(msg.content);
    if (!data || typeof data.reply !== 'string' || !data.reply.trim()) return null;
    return data;
  } catch (e) {
    return null;
  }
}

function normalizeOrder(order, products) {
  const o = order && typeof order === 'object' ? order : {};
  const byName = new Map((products || []).map((p) => [norm(p.name), p]));
  const items = (Array.isArray(o.items) ? o.items : [])
    .map((i) => ({
      product: cleanText(i?.product, 120),
      quantity: Number(i?.quantity),
      unit: cleanText(i?.unit, 40),
      note: cleanText(i?.note, 200),
    }))
    .filter((i) => i.product && Number.isFinite(i.quantity) && i.quantity > 0)
    .slice(0, 30);
  let total = 0;
  let allMatched = items.length > 0;
  for (const i of items) {
    const p = byName.get(norm(i.product));
    i.matched = Boolean(p);
    if (p) {
      i.unit_price_lyd = Number(p.price_lyd);
      total += Number(p.price_lyd) * i.quantity;
    } else {
      allMatched = false;
    }
  }
  const fulfillment = o.fulfillment === 'pickup' || o.fulfillment === 'delivery' ? o.fulfillment : null;
  return {
    status: ['none', 'collecting', 'complete'].includes(o.status) ? o.status : 'none',
    customer_name: cleanText(o.customer_name, 120),
    phone: cleanText(o.phone, 40),
    items,
    fulfillment,
    address: fulfillment === 'delivery' ? cleanText(o.address, 300) : null,
    needed_at: cleanText(o.needed_at, 120),
    notes: cleanText(o.notes, 500),
    estimated_total_lyd: allMatched ? Math.round(total * 1000) / 1000 : null,
  };
}

function fingerprint(o) {
  const items = o.items
    .map((i) => [norm(i.product), i.quantity, norm(i.unit), norm(i.note)])
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  const key = JSON.stringify([items, o.fulfillment, norm(o.address), norm(o.needed_at)]);
  return crypto.createHash('sha256').update(key).digest('hex');
}

const data = parse($json);
let reply = FALLBACK;
let order = normalizeOrder(null, req.products);
let handoff = { needed: true, reason: $json.error ? 'تعطل الاتصال بالذكاء الاصطناعي' : 'رد غير صالح من الذكاء الاصطناعي' };
if (data) {
  reply = data.reply.trim().slice(0, 4000);
  order = normalizeOrder(data.order, req.products);
  handoff = {
    needed: Boolean(data.handoff?.needed),
    reason: cleanText(data.handoff?.reason, 300),
  };
}
const orderComplete = order.status === 'complete' && order.items.length > 0;
// استهلاك التوكنز لكل رد (cached = ما خصمه OpenAI من التخزين المؤقت للبداية الثابتة)
const usage = $json.usage || {};

return {
  json: {
    contact_id: req.contact_id,
    wa_id: req.wa_id,
    contact_name: req.contact_name,
    reply,
    order,
    order_complete: orderComplete,
    fingerprint: orderComplete ? fingerprint(order) : null,
    handoff_needed: handoff.needed,
    handoff_reason: handoff.reason,
    ai_ok: Boolean(data),
    // آخر صورة للطلب تُحفظ للزبون وتُعطى للوكيل في الرسالة التالية (بدل إرسال محادثة أطول)
    order_state: data && order.items.length ? JSON.stringify(order) : null,
    usage: {
      model: $json.model || null,
      prompt_tokens: Number.isInteger(usage.prompt_tokens) ? usage.prompt_tokens : null,
      cached_tokens: Number.isInteger(usage.prompt_tokens_details?.cached_tokens) ? usage.prompt_tokens_details.cached_tokens : null,
      completion_tokens: Number.isInteger(usage.completion_tokens) ? usage.completion_tokens : null,
    },
  },
};
