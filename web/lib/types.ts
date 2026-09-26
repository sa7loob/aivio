export type Role = "owner" | "admin" | "agent";

export interface TenantInfo {
  tenant_id: string;
  name: string;
  slug: string | null;
  role: Role;
  subscription_status: string | null;
}

export interface Me {
  user: { id: string; email: string; full_name: string; phone: string | null };
  tenants: TenantInfo[];
}

export type ConversationMode = "bot" | "human" | "closed";
export type Channel = "whatsapp" | "messenger" | "instagram" | "tiktok" | "manual";

export interface ConversationItem {
  id: string;
  channel: Channel;
  channel_name: string | null;
  mode: ConversationMode;
  bot_paused_until: string | null;
  assigned_user_id: string | null;
  assigned_name: string | null;
  takeover_by: string | null;
  takeover_at: string | null;
  unread_count: number;
  last_message_at: string | null;
  last_message_preview: string | null;
  last_message_direction: "inbound" | "outbound" | null;
  last_inbound_at: string | null;
  contact_id: string;
  contact_name: string | null;
  contact_phone: string | null;
  has_open_lead: boolean;
}

export interface ConversationDetail extends ConversationItem {
  reply_window_open: boolean;
}

export type DeliveryStatus = "pending" | "sending" | "sent" | "failed" | "cancelled" | null;

export interface Message {
  id: string;
  direction: "inbound" | "outbound";
  sender_type: "customer" | "bot" | "staff" | "system";
  msg_type: string;
  text_content: string | null;
  created_at: string;
  client_msg_id: string | null;
  delivery_status: DeliveryStatus;
  delivery_error: string | null;
}

export interface Page<T> {
  items: T[];
  next_cursor?: string | null;
  older_cursor?: string | null;
}

export interface Staff {
  id: string;
  full_name: string;
  role: string;
  user_id: string | null;
  whatsapp_phone: string | null;
  notify_on_new_lead: boolean;
  is_active: boolean;
}

export type LeadStatus = "new" | "contacted" | "qualified" | "booked" | "lost" | "spam";

export interface Lead {
  id: string;
  status: LeadStatus;
  full_name: string | null;
  phone_e164: string | null;
  city: string | null;
  adults: number | null;
  children: number | null;
  infants: number | null;
  room_type_pref: string | null;
  preferred_period: string | null;
  notes: string | null;
  lost_reason: string | null;
  source_channel: string | null;
  collected_by: string | null;
  package_id: string | null;
  package_title: string | null;
  departure_id: string | null;
  depart_date: string | null;
  assigned_to: string | null;
  assigned_name: string | null;
  conversation_id: string | null;
  created_at: string;
  updated_at: string | null;
  notified_at: string | null;
  first_contact_at: string | null;
}

export interface LeadEvent {
  id: string;
  event_type: string;
  data: Record<string, unknown>;
  actor_type: "bot" | "staff" | "system";
  actor_name: string | null;
  created_at: string;
}

export interface LeadDetail extends Lead {
  events: LeadEvent[];
}

export interface Member {
  user_id: string;
  full_name: string;
  email: string;
  role: Role;
  status: string;
  created_at: string;
}

export interface Invitation {
  id: string;
  role: Role;
  email: string | null;
  note: string | null;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

export interface ChannelAccount {
  id: string;
  channel: Channel;
  external_id: string;
  display_name: string | null;
  status: string;
  is_test: boolean;
  last_checked_at: string | null;
  last_error: string | null;
  created_at: string;
}

export interface TenantEvent {
  type: "ready" | "resync" | "message" | "conversation" | "lead" | "message_status";
  id?: string;
  conversation_id?: string;
}
