"""Application settings (read once from environment / .env)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = Field("dev", pattern="^(dev|staging|prod)$")
    log_level: str = "INFO"

    # --- Database: دور التشغيل app_user فقط (NOBYPASSRLS)
    database_url: str = Field(..., description="postgresql+asyncpg://app_user:...@host/db")
    db_pool_size: int = 10

    # --- Meta
    meta_app_secret: SecretStr                      # للتحقق من X-Hub-Signature-256
    meta_verify_token: SecretStr                    # نفس القيمة المسجّلة في لوحة Meta
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_graph_api_version: str = "v25.0"          # ثبّت الإصدار الحالي من Meta changelog
    meta_http_timeout_seconds: float = 10.0
    webhook_max_body_bytes: int = 1_000_000

    # --- لوحة الإدارة الداخلية (عملية منفصلة بدور app_admin)
    admin_database_url: str | None = None           # postgresql+asyncpg://app_admin:...
    voucher_pepper: SecretStr | None = None         # سر HMAC لرموز القسائم (لا يُغيَّر بعد الإصدار)
    meta_app_id: str | None = None                  # لـ debug_token عند فحص صحة التوكنات

    # --- الهوية والجلسات (المرحلة 5)
    session_ttl_days: int = 30
    session_cookie_name: str = "sid"
    cookie_secure: bool = True                      # false فقط في التطوير المحلي بدون HTTPS
    # أصول الواجهة المسموح لها بطلبات تغيير عبر الكوكي (حماية CSRF)، مثال: ["https://app.example.ly"]
    web_allowed_origins: list[str] = Field(default_factory=list)
    signup_plan_code: str = "starter"               # خطة التسجيل الذاتي (يُنشئها المشرف مسبقاً)
    signup_trial_days: int = 14
    invitation_ttl_days: int = 7

    # --- الربط الذاتي مع Meta (المرحلة 5)
    meta_es_config_id: str | None = None            # Embedded Signup configuration (WhatsApp)
    meta_fb_login_config_id: str | None = None      # Facebook Login for Business (Pages + IG)
    meta_oauth_redirect_uri: str | None = None      # يُترك فارغاً مع JS SDK
    onboarding_session_minutes: int = 20
    public_base_url: str = "http://localhost:8000"  # يظهر في رابط حالة حذف البيانات لـ Meta
    password_reset_ttl_hours: int = 24
    channel_health_every_hours: int = 6

    # --- تشفير توكنات القنوات (Fernet key: base64 32 bytes)
    token_encryption_key: SecretStr

    # --- Worker
    worker_poll_interval_seconds: float = 0.5
    worker_event_batch: int = 50
    worker_event_lease_seconds: int = 120
    worker_conversation_batch: int = 20
    worker_conversation_lease_seconds: int = 120
    worker_outbound_batch: int = 50
    worker_outbound_lease_seconds: int = 60
    worker_max_attempts: int = 8
    reply_debounce_seconds: float = 4.0

    # --- LLM (OpenAI). الموديل قابل للتغيير من .env دون تعديل الكود
    openai_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o"
    llm_temperature: float | None = 0.3   # اتركها فارغة لموديلات لا تدعمها
    llm_max_output_tokens: int = 600
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1024      # يجب أن يطابق vector(1024) في knowledge_chunks

    # --- Agent
    agent_max_tool_iterations: int = 5
    agent_history_messages: int = 20
    agent_price_stale_days: int = 14      # سعر أقدم من هذا => البوت يذكر أنه يحتاج تأكيد
    agent_knowledge_top_k: int = 4
    agent_handoff_pause_hours: int = 12   # إيقاف البوت في المحادثة بعد التحويل لموظف
    # قالب واتساب لإشعار الموظف (يجب اعتماده من Meta مسبقاً، فئة Utility)
    lead_template_name: str = "new_lead"
    lead_template_language: str = "ar"

    # --- المرحلة 7a: مهام الذكاء الاصطناعي في الخلفية (ai_jobs)
    worker_ai_batch: int = 5
    worker_ai_lease_seconds: int = 180
    # تفريغ الرسائل الصوتية
    voice_transcription_enabled: bool = True
    transcription_model: str = "gpt-4o-transcribe"
    transcription_language: str = "ar"
    transcription_max_wait_seconds: float = 45.0   # أقصى تأخير لرد البوت بانتظار التفريغ
    voice_max_bytes: int = 3_000_000               # أكبر من هذا => لا تفريغ (حد للتكلفة)
    media_tmp_dir: str | None = None               # ملفات صوت مؤقتة تُحذف بعد التفريغ (الافتراضي: /tmp)
    # البروشور => كتالوج مسودة
    catalog_extraction_model: str = "gpt-4o"
    catalog_extraction_max_output_tokens: int = 8000
    catalog_import_max_bytes: int = 10_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
