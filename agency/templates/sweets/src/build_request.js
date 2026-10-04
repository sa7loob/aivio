// يبني طلب OpenAI: تعليمات المحل + القائمة من قاعدة العميل + آخر رسائل المحادثة، ومخرجات JSON منظمة.
// (وضع: مرة لكل item)
const settings = $('Settings').first().json;
const ctx = $json;
const SYSTEM_TEMPLATE = __SYSTEM_PROMPT__;
const RESPONSE_SCHEMA = __RESPONSE_SCHEMA__;
const WEEKDAYS = ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];

function oneLine(value, max) {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);
}

function libyaNow(now) {
  const t = new Date(now.getTime() + 2 * 3600 * 1000);   // ليبيا: UTC+2 طوال السنة
  const iso = t.toISOString();
  return { weekday: WEEKDAYS[t.getUTCDay()], date: iso.slice(0, 10), time: iso.slice(11, 16) };
}

function money(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
}

function menuText(products) {
  if (!products || !products.length) return '(القائمة فاضية حالياً: قول للزبون إن المحل حيرد عليه، و handoff.needed = true)';
  const groups = new Map();
  for (const p of products) {
    if (!groups.has(p.category)) groups.set(p.category, []);
    let line = `• ${p.name}: ${money(p.price_lyd)} د.ل لكل ${p.unit}`;
    if (p.min_qty && Number(p.min_qty) !== 1) line += ` (أقل طلب ${money(p.min_qty)} ${p.unit})`;
    if (p.description) line += ` — ${oneLine(p.description, 200)}`;
    groups.get(p.category).push(line);
  }
  return [...groups].map(([cat, lines]) => `*${cat}*\n${lines.join('\n')}`).join('\n\n');
}

function factsText(s) {
  const facts = [
    ['أوقات العمل', s.opening_hours],
    ['العنوان', s.address],
    ['التوصيل', s.delivery_info],
    ['الطلب المسبق', s.order_notice],
    ['تعليمات المحل', s.extra_instructions],
  ].filter(([, v]) => v && String(v).trim());
  return facts.length ? facts.map(([k, v]) => `- ${k}: ${oneLine(v, 500)}`).join('\n') : '- (ما فيش معلومات إضافية)';
}

function historyMessages(history) {
  const out = [];
  for (const m of history || []) {
    let content;
    if (m.direction === 'out') {
      content = m.text || '';
    } else if (m.type === 'audio') {
      content = m.text ? `[رسالة صوتية: ${m.text}]`
        : (m.media_failed ? '[رسالة صوتية ما وضحتش]' : '[رسالة صوتية]');
    } else if (m.type === 'other') {
      content = `[${m.wa_type || 'مرفق'}]${m.text ? ' ' + m.text : ''}`;
    } else {
      content = m.text || '';
    }
    if (!content) continue;
    out.push({ role: m.direction === 'out' ? 'assistant' : 'user', content });
  }
  return out;
}

function fill(template, vars) {
  return template.replace(/\{(\w+)\}/g, (all, key) => (key in vars ? vars[key] : all));
}

const now = libyaNow(new Date());
const system = fill(SYSTEM_TEMPLATE, {
  business_name: oneLine(settings.business_name, 100),
  facts: factsText(settings),
  menu: menuText(ctx.products),
  weekday: now.weekday,
  date: now.date,
  time: now.time,
  customer_name: oneLine(ctx.contact_name, 60) || 'غير معروف',
  customer_phone: '+' + ctx.wa_id,
});

return {
  json: {
    contact_id: ctx.contact_id,
    wa_id: ctx.wa_id,
    contact_name: ctx.contact_name || null,
    products: ctx.products || [],
    request: {
      model: settings.chat_model,
      temperature: 0.3,
      max_completion_tokens: 800,
      messages: [{ role: 'system', content: system }, ...historyMessages(ctx.history)],
      response_format: {
        type: 'json_schema',
        json_schema: { name: 'shop_reply', strict: true, schema: RESPONSE_SCHEMA },
      },
    },
  },
};
