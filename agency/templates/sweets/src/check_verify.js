// تحقق Meta عند ربط الـ webhook: يعيد hub.challenge فقط إذا طابق hub.verify_token القيمة المحفوظة.
const q = $('Webhook GET').first().json.query || {};
const token = $input.first().json.meta_verify_token;
const ok = q['hub.mode'] === 'subscribe' && Boolean(token) && q['hub.verify_token'] === token;
return [{ json: { ok, challenge: ok ? String(q['hub.challenge'] ?? '') : 'forbidden' } }];
