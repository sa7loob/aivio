// تنبيه صاحب المحل على واتساب: طلب جديد مكتمل، أو زبون يحتاج رداً بشرياً.
// قالب معتمد (owner_template_name) => يصل في أي وقت. بدونه رسالة نصية تصل فقط داخل نافذة 24 ساعة.
// (وضع: مرة لكل item؛ المدخل صف الطلب الجديد {order_id} أو رد محفوظ للتحويل)
const settings = $('Settings').first().json;
const r = $('Parse reply').item.json;
const orderId = $json.order_id ?? null;
const kind = orderId ? 'order' : 'handoff';

function oneLine(value, max) {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);
}

function money(value) {
  const n = Number(value);
  return Number.isInteger(n) ? String(n) : n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
}

const o = r.order;
const name = oneLine(o.customer_name || r.contact_name, 80) || 'زبون';
const phone = '+' + oneLine(o.phone || r.wa_id, 30).replace(/^\+/, '');
const items = o.items.map((i) => `${i.product} × ${money(i.quantity)} ${i.unit || ''}${i.note ? ` (${i.note})` : ''}`.trim());
const fulfillment = o.fulfillment === 'delivery' ? `توصيل: ${o.address || 'العنوان غير محدد'}`
  : (o.fulfillment === 'pickup' ? 'استلام من المحل' : 'الاستلام غير محدد');
const when = o.needed_at || 'الموعد غير محدد';
const total = o.estimated_total_lyd != null ? `حوالي ${money(o.estimated_total_lyd)} د.ل بدون التوصيل` : null;
const chatLink = `wa.me/${r.wa_id}`;

let text;
let params;
if (kind === 'order') {
  text = [
    `🧁 طلب جديد — ${settings.business_name}`,
    `👤 ${name}   📞 ${phone}`,
    ...items.map((i) => `• ${i}`),
    `🚚 ${fulfillment}`,
    `🕒 ${when}`,
    total ? `💰 ${total}` : null,
    o.notes ? `📝 ${o.notes}` : null,
    `للرد على الزبون: ${chatLink}`,
  ].filter(Boolean).join('\n');
  params = [name, phone, oneLine(items.join('، '), 500), oneLine([fulfillment, when, total].filter(Boolean).join(' — '), 200)];
} else {
  const reason = r.handoff_reason || 'طلب التواصل مع المحل';
  text = [
    `🔔 زبون يحتاج رد منكم — ${settings.business_name}`,
    `👤 ${name}   📞 ${phone}`,
    `السبب: ${reason}`,
    `للرد على الزبون: ${chatLink}`,
  ].join('\n');
  params = [name, phone, oneLine(`يحتاج رد منكم: ${reason}`, 500), chatLink];
}

const body = settings.owner_template_name
  ? {
    messaging_product: 'whatsapp',
    to: settings.owner_phone,
    type: 'template',
    template: {
      name: settings.owner_template_name,
      language: { code: settings.owner_template_lang || 'ar' },
      // متغيرات القالب: سطر واحد بدون أسطر جديدة، ومجموع نص القالب أقل من 1024 حرفاً (شروط واتساب)
      components: [{ type: 'body', parameters: params.map((p) => ({ type: 'text', text: p || '-' })) }],
    },
  }
  : { messaging_product: 'whatsapp', to: settings.owner_phone, type: 'text', text: { preview_url: false, body: text } };

return { json: { kind, order_id: orderId, body } };
