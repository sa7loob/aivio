// يتحقق من توقيع Meta (X-Hub-Signature-256 بـ App Secret العميل) ثم يستخرج رسائل الزبائن.
// توقيع خاطئ، أو رقم واتساب غير رقم العميل، أو تحديثات حالة (statuses) => لا شيء يُكمل.
const crypto = require('crypto');
const settings = $input.first().json;
const { raw, signature } = $('Read body').first().json;

function validSignature(body, sig, secret) {
  if (!body || !secret || !sig || !sig.startsWith('sha256=')) return false;
  const expected = 'sha256=' + crypto.createHmac('sha256', secret).update(body, 'utf8').digest('hex');
  const a = Buffer.from(sig);
  const b = Buffer.from(expected);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function textOf(m) {
  return m.text?.body
    ?? m.button?.text
    ?? m.interactive?.button_reply?.title
    ?? m.interactive?.list_reply?.title
    ?? m.image?.caption
    ?? m.video?.caption
    ?? m.document?.caption
    ?? null;
}

function extract(payload, phoneNumberId) {
  const out = [];
  for (const entry of payload?.entry ?? []) {
    for (const change of entry?.changes ?? []) {
      if (change?.field !== 'messages') continue;
      const value = change.value ?? {};
      if (String(value.metadata?.phone_number_id ?? '') !== String(phoneNumberId)) continue;
      const names = {};
      for (const c of value.contacts ?? []) names[c.wa_id] = c.profile?.name ?? null;
      for (const m of value.messages ?? []) {
        if (!m?.id || !m?.from) continue;
        const isAudio = m.type === 'audio';
        const text = isAudio ? null : textOf(m);
        out.push({
          wa_message_id: String(m.id),
          wa_id: String(m.from),
          name: names[m.from] ? String(names[m.from]).slice(0, 120) : null,
          type: isAudio ? 'audio' : (text ? 'text' : 'other'),
          wa_type: m.type ? String(m.type) : null,
          text: text ? String(text).slice(0, 4000) : null,
          media_id: isAudio && m.audio?.id ? String(m.audio.id) : null,
        });
      }
    }
  }
  return out;
}

if (!validSignature(raw, signature, settings.meta_app_secret)) return [];
let payload;
try {
  payload = JSON.parse(raw);
} catch (e) {
  return [];
}
return extract(payload, settings.phone_number_id).map((json) => ({ json }));
