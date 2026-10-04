// يقرأ جسم طلب Meta كما وصل حرفياً: التوقيع محسوب على البايتات الأصلية، وليس على JSON بعد تحليله.
const item = $input.first();
const headers = item.json.headers || {};
let raw = '';
if (item.binary && item.binary.data) {
  raw = (await this.helpers.getBinaryDataBuffer(0, 'data')).toString('utf8');
}
return [{ json: { raw, signature: String(headers['x-hub-signature-256'] || '') } }];
