// OpenAI يحدد صيغة الصوت من امتداد اسم الملف، ورابط واتساب لا يحمل امتداداً.
// (وضع: مرة لكل item)
const EXT = {
  'audio/ogg': 'ogg', 'audio/opus': 'ogg', 'audio/mpeg': 'mp3', 'audio/mp4': 'm4a', 'audio/m4a': 'm4a',
  'audio/x-m4a': 'm4a', 'audio/aac': 'aac', 'audio/amr': 'amr', 'audio/webm': 'webm', 'audio/wav': 'wav',
};
const mime = String($('Get media URL').item.json.mime_type || $input.item.binary?.data?.mimeType || '')
  .split(';')[0].trim().toLowerCase();
const ext = EXT[mime] || 'ogg';
const item = $input.item;
item.binary.data.fileName = `voice.${ext}`;
item.binary.data.fileExtension = ext;
return item;
