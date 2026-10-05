// يبني طلب OpenAI: تعليمات المحل + القائمة من قاعدة العميل + السياق + آخر رسائل المحادثة، ومخرجات JSON منظمة.
// توفير التوكنز:
//  - الرسالة الأولى (التعليمات والقائمة) ثابتة لكل الزبائن => OpenAI يخزّنها (prompt caching) ويخصم سعرها.
//    كل ما يتغير (الوقت، الزبون، الطلب الجاري) في رسالة ثانية قصيرة بعدها.
//  - طلب «المنيو/الأسعار» وحده => menu_request، ويُرد عليه من القاعدة مباشرة بدون OpenAI.
// (وضع: مرة لكل item)
const settings = $('Settings').first().json;
const ctx = $json;
const SYSTEM_TEMPLATE = __SYSTEM_PROMPT__;
const CONTEXT_TEMPLATE = __CONTEXT_PROMPT__;
const RESPONSE_SCHEMA = __RESPONSE_SCHEMA__;
const WEEKDAYS = ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];
const MENU_PART_MAX = 3500;            // حد رسالة واتساب 4096 حرفاً

function oneLine(value, max) {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);
}

function norm(s) {
  return String(s ?? '')
    .replace(/[ً-ْـ]/g, '')
    .replace(/[أإآ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
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

function menuSections(products) {
  const groups = new Map();
  for (const p of products || []) {
    if (!groups.has(p.category)) groups.set(p.category, []);
    let line = `• ${p.name}: ${money(p.price_lyd)} د.ل لكل ${p.unit}`;
    if (p.min_qty && Number(p.min_qty) !== 1) line += ` (أقل طلب ${money(p.min_qty)} ${p.unit})`;
    if (p.description) line += ` — ${oneLine(p.description, 200)}`;
    groups.get(p.category).push(line);
  }
  return [...groups].map(([cat, lines]) => `*${cat}*\n${lines.join('\n')}`);
}

function menuText(products) {
  if (!products || !products.length) return '(القائمة فاضية حالياً: قول للزبون إن المحل حيرد عليه، و handoff.needed = true)';
  return menuSections(products).join('\n\n');
}

// قائمة الأسعار للزبون مباشرة (بدون OpenAI)، مقسّمة على رسائل إذا طالت
function menuParts(products, businessName) {
  const head = `🧁 *قائمة أسعار ${oneLine(businessName, 100)}* (بالدينار الليبي)`;
  const tail = 'للطلب ابعثلنا: الأصناف والكمية، واستلام ولا توصيل، واليوم والوقت 🌸';
  // فئة طويلة جداً تُقسم على أسطرها مع تكرار عنوانها
  const blocks = [];
  for (const section of menuSections(products)) {
    const [title, ...lines] = section.split('\n');
    let block = title;
    for (const line of lines) {
      if (block.length + line.length + 1 > MENU_PART_MAX - head.length - 4) {
        blocks.push(block);
        block = `${title} (تكملة)`;
      }
      block += '\n' + line;
    }
    blocks.push(block);
  }
  const parts = [];
  let current = head;
  for (const block of blocks) {
    if (current.length + block.length + 2 > MENU_PART_MAX && current !== head) {
      parts.push(current);
      current = block;
    } else {
      current += '\n\n' + block;
    }
  }
  parts.push(current + '\n\n' + tail);
  return parts;
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

function orderText(order) {
  if (!order || !Array.isArray(order.items) || !order.items.length) return 'ما فيش';
  const items = order.items.map((i) => `${i.product} × ${money(i.quantity)} ${i.unit || ''}${i.note ? ` (${i.note})` : ''}`.trim());
  const parts = [items.join('، ')];
  if (order.fulfillment === 'delivery') parts.push(`توصيل: ${order.address || 'العنوان ناقص'}`);
  if (order.fulfillment === 'pickup') parts.push('استلام من المحل');
  if (order.needed_at) parts.push(`الموعد: ${order.needed_at}`);
  if (order.customer_name) parts.push(`الاسم: ${order.customer_name}`);
  if (order.notes) parts.push(`ملاحظات: ${order.notes}`);
  const status = order.status === 'complete' ? 'مكتمل ووصل للمحل' : 'لسه ما تأكدش';
  return oneLine(`${parts.join(' | ')} (${status})`, 1200);
}

// رسائل الزبون الجديدة (بعد آخر رد منّا) طلب للقائمة فقط؟ مثل: «المنيو»، «نبي نشوف الأسعار»، «شن عندكم؟»
function isMenuRequest(history, products) {
  const pending = [];
  for (let i = (history || []).length - 1; i >= 0 && history[i].direction === 'in'; i--) pending.unshift(history[i]);
  if (!pending.length || pending.some((m) => !m.text)) return false;
  const text = norm(pending.map((m) => m.text).join(' '));
  const words = text.split(' ').filter(Boolean);
  if (!words.length || words.length > 8) return false;
  const asksMenu = /(^| )و?(ب|ل)?(ال)?(منيو|مينيو|قائمه|اسعار|كتالوج)( |$)/.test(text)
    || /(^| )(menu|prices?|price list)( |$)/.test(text)
    || /(^| )(شن|شنو|شو|شني) عندكم( |$)/.test(text);
  if (!asksMenu) return false;
  // سؤال عن صنف محدد («أسعار البقلاوة») يحتاج رداً مخصصاً من الوكيل
  const heads = new Set((products || []).map((p) => norm(p.name).split(' ')[0].replace(/^ال/, '')).filter((w) => w.length > 2));
  return !words.some((w) => heads.has(w.replace(/^(و|ب|ل)?ال/, '')));
}

const now = libyaNow(new Date());
const system = SYSTEM_TEMPLATE.replace(/\{(business_name|facts|menu)\}/g, (all, key) => ({
  business_name: oneLine(settings.business_name, 100),
  facts: factsText(settings),
  menu: menuText(ctx.products),
}[key]));
const context = CONTEXT_TEMPLATE.replace(/\{(\w+)\}/g, (all, key) => ({
  weekday: now.weekday,
  date: now.date,
  time: now.time,
  customer_name: oneLine(ctx.contact_name, 60) || 'غير معروف',
  customer_phone: '+' + ctx.wa_id,
  current_order: orderText(ctx.current_order),
}[key] ?? all));
const menuRequest = isMenuRequest(ctx.history, ctx.products) && (ctx.products || []).length > 0;

return {
  json: {
    contact_id: ctx.contact_id,
    wa_id: ctx.wa_id,
    contact_name: ctx.contact_name || null,
    products: ctx.products || [],
    menu_request: menuRequest,
    menu_parts: menuRequest ? menuParts(ctx.products, settings.business_name) : [],
    request: {
      model: settings.chat_model,
      temperature: 0.3,
      max_completion_tokens: 600,
      messages: [
        { role: 'system', content: system },
        { role: 'system', content: context },
        ...historyMessages(ctx.history),
      ],
      response_format: {
        type: 'json_schema',
        json_schema: { name: 'shop_reply', strict: true, schema: RESPONSE_SCHEMA },
      },
    },
  },
};
