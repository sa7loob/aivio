-- بيانات اختبار: وكالتان بأسماء برامج متشابهة عمداً لاختبار العزل.
-- يُشغَّل بدور app_owner (المالك لا يخضع لـ RLS).
\set ON_ERROR_STOP on
BEGIN;

TRUNCATE tenants CASCADE;
TRUNCATE webhook_events;
TRUNCATE plans, voucher_batches, receipt_counters, admin_users, users, meta_data_deletion_requests CASCADE;

INSERT INTO tenants (id, slug, name) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', 'noor-travel',  'وكالة النور للحج والعمرة'),
  ('bbbbbbbb-0000-0000-0000-000000000002', 'safa-travel',  'وكالة الصفا للسياحة');

INSERT INTO staff_users (id, tenant_id, full_name, whatsapp_phone, role) VALUES
  ('aaaaaaaa-0000-0000-0000-0000000000f1', 'aaaaaaaa-0000-0000-0000-000000000001', 'موظف مبيعات النور', '+218911234567', 'sales'),
  ('bbbbbbbb-0000-0000-0000-0000000000f2', 'bbbbbbbb-0000-0000-0000-000000000002', 'موظف مبيعات الصفا', '+218921234567', 'sales');

INSERT INTO channel_accounts (id, tenant_id, channel, external_id, is_test) VALUES
  ('aaaaaaaa-0000-0000-0000-0000000000c1', 'aaaaaaaa-0000-0000-0000-000000000001', 'whatsapp',  'PNID-NOOR-TEST', true),
  ('aaaaaaaa-0000-0000-0000-0000000000c2', 'aaaaaaaa-0000-0000-0000-000000000001', 'instagram', 'IG-NOOR',        false),
  ('bbbbbbbb-0000-0000-0000-0000000000c3', 'bbbbbbbb-0000-0000-0000-000000000002', 'messenger', 'PAGE-SAFA',      false);

-- برامج النور
INSERT INTO packages (id, tenant_id, code, kind, title, season_label, status, duration_days,
                      nights_makkah, nights_madinah, departure_city, airline, description) VALUES
  ('aaaaaaaa-0000-0000-0000-0000000000a1', 'aaaaaaaa-0000-0000-0000-000000000001', 'UMR-RAM', 'umrah',
   'عمرة العشر الأواخر من رمضان - 12 يوم', 'رمضان 1448', 'active', 12, 8, 4, 'طرابلس', 'الخطوط الليبية',
   'فنادق قريبة من الحرم مع السحور والإفطار'),
  ('aaaaaaaa-0000-0000-0000-0000000000a2', 'aaaaaaaa-0000-0000-0000-000000000001', 'UMR-MAWLID', 'umrah',
   'عُمْرَة المَولِد النبوي الشريف - 15 يوم', 'ربيع الأول', 'active', 15, 10, 5, 'بنغازي', 'الأجنحة',
   'برنامج اقتصادي'),
  ('aaaaaaaa-0000-0000-0000-0000000000a3', 'aaaaaaaa-0000-0000-0000-000000000001', 'HAJJ-VIP', 'hajj',
   'حج الخمس نجوم', '1448', 'active', 21, 14, 7, 'طرابلس', 'الخطوط الليبية', NULL),
  ('aaaaaaaa-0000-0000-0000-0000000000a4', 'aaaaaaaa-0000-0000-0000-000000000001', 'DRAFT-1', 'tourism',
   'رحلة إسطنبول', NULL, 'draft', 7, 0, 0, 'طرابلس', NULL, NULL);

-- برنامج الصفا باسم مشابه جداً (يجب ألا يظهر للنور أبداً)
INSERT INTO packages (id, tenant_id, kind, title, status) VALUES
  ('bbbbbbbb-0000-0000-0000-0000000000b1', 'bbbbbbbb-0000-0000-0000-000000000002', 'umrah',
   'عمرة رمضان الاقتصادية', 'active');

INSERT INTO package_aliases (tenant_id, package_id, alias) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1', 'عمرة رمضان'),
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1', 'العشر الأواخر'),
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a2', 'عمرة شهر 9 الميلادي'),
  ('bbbbbbbb-0000-0000-0000-000000000002', 'bbbbbbbb-0000-0000-0000-0000000000b1', 'عمرة رمضان');

INSERT INTO package_hotels (tenant_id, package_id, city, hotel_name, stars, distance_note, nights) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1', 'makkah',  'فندق المثال مكة', 4, '400 متر من الحرم', 8),
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1', 'madinah', 'فندق المثال المدينة', 4, '200 متر', 4);

INSERT INTO package_departures (id, tenant_id, package_id, depart_date, return_date,
                                registration_deadline, seats_total, seats_left, status) VALUES
  ('aaaaaaaa-0000-0000-0000-0000000000d1', 'aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1',
   current_date + 60, current_date + 72, current_date + 30, 45, 12, 'open'),
  ('aaaaaaaa-0000-0000-0000-0000000000d2', 'aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a2',
   current_date + 20, current_date + 35, current_date + 5, 30, 3, 'few_left'),
  ('bbbbbbbb-0000-0000-0000-0000000000d3', 'bbbbbbbb-0000-0000-0000-000000000002', 'bbbbbbbb-0000-0000-0000-0000000000b1',
   current_date + 60, current_date + 70, NULL, 40, 40, 'open');

-- سعر عام للبرنامج (departure_id NULL) + سعر خاص بموعد
INSERT INTO package_prices (tenant_id, package_id, departure_id, room_type, traveler_type, amount) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1', NULL, 'quad',   'adult', 9500),
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1', NULL, 'triple', 'adult', 10500),
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1', NULL, 'double', 'adult', 12000),
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1', NULL, 'quad',   'child', 7000),
  ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a2', 'aaaaaaaa-0000-0000-0000-0000000000d2', 'quad', 'adult', 6500),
  ('bbbbbbbb-0000-0000-0000-000000000002', 'bbbbbbbb-0000-0000-0000-0000000000b1', NULL, 'quad', 'adult', 8000);

INSERT INTO contacts (id, tenant_id, channel, external_user_id, display_name) VALUES
  ('aaaaaaaa-0000-0000-0000-0000000000e1', 'aaaaaaaa-0000-0000-0000-000000000001', 'whatsapp', '218913334444', 'أبو محمد'),
  ('bbbbbbbb-0000-0000-0000-0000000000e2', 'bbbbbbbb-0000-0000-0000-000000000002', 'messenger', 'PSID-999', 'زبون الصفا');

INSERT INTO conversations (id, tenant_id, contact_id, channel_account_id, last_inbound_at, reply_due_at) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000011', 'aaaaaaaa-0000-0000-0000-000000000001',
   'aaaaaaaa-0000-0000-0000-0000000000e1', 'aaaaaaaa-0000-0000-0000-0000000000c1', now(), now() - interval '1 second'),
  ('bbbbbbbb-0000-0000-0000-000000000022', 'bbbbbbbb-0000-0000-0000-000000000002',
   'bbbbbbbb-0000-0000-0000-0000000000e2', 'bbbbbbbb-0000-0000-0000-0000000000c3', now(), now() + interval '1 hour');

INSERT INTO leads (tenant_id, contact_id, package_id, full_name, phone_e164, adults, source_channel) VALUES
  ('bbbbbbbb-0000-0000-0000-000000000002', 'bbbbbbbb-0000-0000-0000-0000000000e2',
   'bbbbbbbb-0000-0000-0000-0000000000b1', 'زبون الصفا', '+218925556666', 2, 'messenger');

COMMIT;
