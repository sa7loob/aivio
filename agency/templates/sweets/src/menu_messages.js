// رد «المنيو» بدون OpenAI: صورة المنيو إن وُجد رابطها في الإعدادات، وإلا قائمة الأسعار نصاً من القاعدة.
// (وضع: مرة لكل الـ items؛ القائمة الطويلة = أكثر من رسالة)
const settings = $('Settings').first().json;
const out = [];
for (const item of $input.all()) {
  const { contact_id, wa_id, menu_parts } = item.json;
  const image = String(settings.menu_image_url || '').trim();
  const bodies = image
    ? [{ type: 'image', image: { link: image, caption: `قائمة ${settings.business_name} 🧁\nللطلب ابعثلنا: الأصناف والكمية، واستلام ولا توصيل، واليوم والوقت.` } }]
    : menu_parts.map((body) => ({ type: 'text', text: { preview_url: false, body } }));
  const marker = image ? '[أرسلنا صورة المنيو]' : '[أرسلنا قائمة الأسعار كاملة]';
  bodies.forEach((b, i) => out.push({
    json: {
      contact_id,
      wa_id,
      part: i + 1,
      marker,
      body: { messaging_product: 'whatsapp', recipient_type: 'individual', to: wa_id, ...b },
    },
  }));
}
return out;
