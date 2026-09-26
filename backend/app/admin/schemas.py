"""Request models for the Admin API. المبالغ Decimal بالدينار (3 خانات عشرية كحد أقصى)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

LYD = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
BusinessType = Literal["travel_hajj_umrah", "retail", "services", "other"]
CreditMethod = Literal["bank_transfer", "libyana_balance", "almadar_balance", "cash", "plutu"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def money(value: Any) -> str | None:
    """المبالغ تُعاد كنص ('150.000') لتجنب أخطاء float في الواجهات."""
    return None if value is None else f"{Decimal(value):.3f}"


# ------------------------------------------------------------------ tenants
class OwnerIn(Strict):
    full_name: str = Field(..., min_length=2, max_length=100)
    whatsapp_phone: str | None = Field(None, description="يستقبل إشعارات الـ Leads")
    email: str | None = None


class TenantCreate(Strict):
    slug: str = Field(..., pattern=r"^[a-z0-9-]{3,50}$")
    name: str = Field(..., min_length=2, max_length=150)
    business_type: BusinessType = "travel_hajj_umrah"
    timezone: str = "Africa/Tripoli"
    settings: dict[str, Any] = Field(default_factory=dict,
                                     description="assistant_name, working_hours, address, extra_instructions")
    owner: OwnerIn
    plan_code: str
    trial_days: int = Field(14, ge=0, le=90)


class TenantUpdate(Strict):
    name: str | None = Field(None, min_length=2, max_length=150)
    settings: dict[str, Any] | None = Field(None, description="يُدمج مع الإعدادات الحالية (merge)")
    status: Literal["trial", "active", "suspended", "cancelled"] | None = None


class StaffCreate(Strict):
    full_name: str = Field(..., min_length=2, max_length=100)
    whatsapp_phone: str | None = None
    email: str | None = None
    role: Literal["owner", "manager", "sales", "agent"] = "sales"
    notify_on_new_lead: bool = True


# ------------------------------------------------------------------ channels
class WhatsAppConnect(Strict):
    phone_number_id: str = Field(..., pattern=r"^\d{5,30}$")
    waba_id: str | None = Field(None, pattern=r"^\d{5,30}$")
    access_token: str = Field(..., min_length=20)
    is_test: bool = False
    subscribe_webhooks: bool = True


class FacebookPageConnect(Strict):
    page_id: str = Field(..., pattern=r"^\d{5,30}$")
    page_access_token: str = Field(..., min_length=20)
    include_instagram: bool = True
    subscribe_webhooks: bool = True


class ChannelStatusUpdate(Strict):
    status: Literal["active", "paused", "disconnected"]


# ------------------------------------------------------------------ billing
class PlanPriceIn(Strict):
    period_months: Literal[1, 3, 6, 12]
    price_lyd: LYD
    is_active: bool = True


class PlanCreate(Strict):
    code: str = Field(..., pattern=r"^[a-z0-9_-]{2,40}$")
    name: str
    description: str | None = None
    limits: dict[str, int] = Field(default_factory=dict,
                                   description="conversations_month, seats, channels, knowledge_mb")
    prices: list[PlanPriceIn] = Field(..., min_length=1)
    sort_order: int = 0


class PlanUpdate(Strict):
    name: str | None = None
    description: str | None = None
    limits: dict[str, int] | None = None
    is_active: bool | None = None


class WalletCredit(Strict):
    amount_lyd: LYD
    method: CreditMethod
    reference: str | None = Field(None, max_length=120, description="رقم الحوالة / الإيصال")
    note: str | None = Field(None, max_length=300)


class WalletAdjust(Strict):
    direction: Literal["credit", "debit"]
    amount_lyd: LYD
    reason: str = Field(..., min_length=5, max_length=300)


class SubscriptionRenew(Strict):
    plan_price_id: UUID


class SubscriptionUpdate(Strict):
    auto_renew: bool | None = None
    renewal_price_id: UUID | None = None
    grace_days: int | None = Field(None, ge=0, le=30)
    status: Literal["suspended", "cancelled", "active"] | None = None


class SubscriptionGrant(Strict):
    days: int = Field(..., ge=1, le=366)
    plan_code: str | None = None
    reason: str = Field(..., min_length=3, max_length=300)


class ManualPaymentCreate(Strict):
    method: Literal["bank_transfer", "libyana_balance", "almadar_balance", "cash"]
    amount_lyd: LYD
    sender_name: str | None = None
    sender_phone: str | None = None
    bank_reference: str | None = None
    proof_url: str | None = None
    note: str | None = None


class ManualPaymentReject(Strict):
    reason: str = Field(..., min_length=3, max_length=300)


# ------------------------------------------------------------------ vouchers
class VoucherBatchCreate(Strict):
    distributor_name: str = Field(..., min_length=2, max_length=120)
    denomination_lyd: LYD
    quantity: int = Field(..., ge=1, le=5000)
    expires_at: datetime | None = None


class VoucherBatchVoid(Strict):
    reason: str = Field(..., min_length=3, max_length=300)


class VoucherRedeem(Strict):
    code: str = Field(..., min_length=16, max_length=40)


class OwnerInvitationCreate(Strict):
    email: str | None = Field(None, description="للتوثيق فقط؛ الدعوة تُرسل للمالك عبر واتساب")
    note: str | None = Field(None, max_length=200)


class PasswordResetIssue(Strict):
    email: str
