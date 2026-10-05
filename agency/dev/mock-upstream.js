// بديل محلي لـ WhatsApp Graph API و OpenAI (للتجربة فقط، لا يُستخدم في الإنتاج).
// يسجّل كل طلب ليفحصه سكربت التجربة، ويرفض أي طلب بدون التوكن الصحيح (للتأكد من ربط الـ credentials).
//   /graph/<ver>/<media_id>            GET  رابط وسائط (MEDIA...؛ يحتوي expired => 404)
//   /media/<id>                         GET  بايتات الصوت
//   /graph/<ver>/<phone_id>/messages    POST إرسال رسالة (fail_sends => 500)
//   /openai/v1/audio/transcriptions     POST تفريغ (multipart)
//   /openai/v1/chat/completions         POST رد الوكيل (من طابور completions، أو رد افتراضي)
//   /__mock/requests  GET   /__mock/reset POST
//   /__mock/script    POST {completions, transcript, transcript_delay_ms, fail_sends, fail_chat}
const http = require('http');

const PORT = Number(process.env.PORT || 8080);
const WA_TOKEN = process.env.MOCK_WA_TOKEN || 'test-wa-token';
const OPENAI_KEY = process.env.MOCK_OPENAI_KEY || 'sk-test';
const DEFAULT_REPLY = {
  // نص ثابت مُعلَّم: البديل لا يفهم الرسائل، فلا يُظن أنه رد الذكاء الاصطناعي الحقيقي
  reply: '(رد تجريبي من البديل المحلي، وليس من الذكاء الاصطناعي) مرحبا بيك في حلويات شهرزاد!',
  order: { status: 'none', customer_name: null, phone: null, items: [], fulfillment: null, address: null,
    needed_at: null, notes: null },
  handoff: { needed: false, reason: null },
};

let state;
function reset() {
  state = { requests: [], completions: [], transcript: 'نبي كيلو بقلاوة بالفستق للخميس', transcriptDelayMs: 0,
    failSends: false, failChat: false, seq: 0 };
}
reset();

function send(res, status, body, type = 'application/json') {
  const data = Buffer.isBuffer(body) ? body : Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
  res.writeHead(status, { 'Content-Type': type, 'Content-Length': data.length });
  res.end(data);
}

function multipartFields(body, contentType) {
  const boundary = /boundary=(?:"([^"]+)"|([^;]+))/.exec(contentType || '');
  if (!boundary) return {};
  const parts = body.toString('latin1').split('--' + (boundary[1] || boundary[2]));
  const fields = {};
  for (const part of parts) {
    const m = /name="([^"]+)"(?:; filename="([^"]*)")?/.exec(part);
    if (!m) continue;
    const value = part.slice(part.indexOf('\r\n\r\n') + 4).replace(/\r\n$/, '');
    fields[m[1]] = m[2] !== undefined
      ? { filename: m[2], size: Buffer.byteLength(value, 'latin1') }
      : Buffer.from(value, 'latin1').toString('utf8');
  }
  return fields;
}

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    const body = Buffer.concat(chunks);
    const url = new URL(req.url, 'http://mock');
    const path = url.pathname;
    const auth = req.headers.authorization || '';
    const record = (kind, data) => state.requests.push({ kind, method: req.method, path, auth, ...data });

    if (path === '/__mock/requests') return send(res, 200, state.requests);
    if (path === '/__mock/reset' && req.method === 'POST') { reset(); return send(res, 200, { ok: true }); }
    if (path === '/__mock/script' && req.method === 'POST') {
      const s = JSON.parse(body.toString() || '{}');
      if (s.completions) state.completions.push(...s.completions);
      if (s.transcript !== undefined) state.transcript = s.transcript;
      if (s.transcript_delay_ms !== undefined) state.transcriptDelayMs = Number(s.transcript_delay_ms);
      if (s.fail_sends !== undefined) state.failSends = Boolean(s.fail_sends);
      if (s.fail_chat !== undefined) state.failChat = Boolean(s.fail_chat);
      return send(res, 200, { ok: true, queued: state.completions.length });
    }

    if (path.startsWith('/graph/') || path.startsWith('/media/')) {
      if (auth !== `Bearer ${WA_TOKEN}`) {
        record('graph_unauthorized', {});
        return send(res, 401, { error: { message: 'Invalid OAuth access token', code: 190 } });
      }
      const parts = path.split('/').filter(Boolean);          // graph, v23.0, id[, messages]
      if (parts[0] === 'media') {
        record('media_download', { id: parts[1] });
        return send(res, 200, Buffer.concat([Buffer.from('OggS'), Buffer.alloc(2044)]), 'audio/ogg');
      }
      if (req.method === 'GET' && parts.length === 3) {
        record('media_url', { id: parts[2] });
        if (parts[2].includes('expired')) return send(res, 404, { error: { message: 'media not found', code: 100 } });
        return send(res, 200, { messaging_product: 'whatsapp', id: parts[2], mime_type: 'audio/ogg; codecs=opus',
          url: `http://${req.headers.host}/media/${parts[2]}`, file_size: 2048 });
      }
      if (req.method === 'POST' && parts[3] === 'messages') {
        const msg = JSON.parse(body.toString());
        record('send', { phone_number_id: parts[2], message: msg });
        if (state.failSends) return send(res, 500, { error: { message: 'mock send failure', code: 1 } });
        state.seq += 1;
        return send(res, 200, { messaging_product: 'whatsapp', contacts: [{ input: msg.to, wa_id: msg.to }],
          messages: [{ id: `wamid.MOCK${state.seq}` }] });
      }
    }

    if (path.startsWith('/openai/')) {
      if (auth !== `Bearer ${OPENAI_KEY}`) {
        record('openai_unauthorized', {});
        return send(res, 401, { error: { message: 'Incorrect API key provided' } });
      }
      if (path.endsWith('/audio/transcriptions')) {
        const fields = multipartFields(body, req.headers['content-type']);
        record('transcription', { fields });
        const text = state.transcript;
        return setTimeout(() => send(res, 200, { text }), state.transcriptDelayMs);
      }
      if (path.endsWith('/chat/completions')) {
        const request = JSON.parse(body.toString());
        record('chat', { request });
        if (state.failChat) return send(res, 500, { error: { message: 'mock upstream failure' } });
        const content = state.completions.length ? state.completions.shift() : DEFAULT_REPLY;
        return send(res, 200, { id: 'chatcmpl-mock', model: request.model, object: 'chat.completion',
          choices: [{ index: 0, finish_reason: 'stop',
            message: { role: 'assistant', content: typeof content === 'string' ? content : JSON.stringify(content) } }],
          usage: { prompt_tokens: 1200, completion_tokens: 80, total_tokens: 1280,
            prompt_tokens_details: { cached_tokens: 1024 } } });
      }
    }
    record('unknown', {});
    return send(res, 404, { error: { message: `mock: no route for ${req.method} ${path}` } });
  });
});

server.listen(PORT, () => console.log(`mock upstream on :${PORT}`));
