-- وكالة تقييم وهمية (بيانات وأسعار تجريبية). يُشغَّل بدور app_owner.
-- التواريخ نسبية لـ current_date حتى تبقى المواعيد مستقبلية دائماً.
BEGIN;

DELETE FROM tenants WHERE slug = 'eval-agency';

INSERT INTO tenants (id, slug, name, status, settings) VALUES
  ('eeeeeeee-0000-0000-0000-000000000001', 'eval-agency', 'وكالة الريان للحج والعمرة (تجريبية)', 'active',
   '{"assistant_name": "مساعد الريان",
     "working_hours": "من السبت للخميس، من 9 الصبح لين 5 العشية",
     "address": "طرابلس - شارع عمر المختار، قرب جامع مولاي محمد (عنوان تجريبي)"}');

INSERT INTO staff_users (tenant_id, full_name, whatsapp_phone, role) VALUES
  ('eeeeeeee-0000-0000-0000-000000000001', 'موظف المبيعات', '+218911112222', 'sales');

INSERT INTO channel_accounts (id, tenant_id, channel, external_id, is_test) VALUES
  ('eeeeeeee-0000-0000-0000-0000000000c1', 'eeeeeeee-0000-0000-0000-000000000001', 'whatsapp', 'EVAL-PNID', true);

INSERT INTO packages (id, tenant_id, code, kind, title, season_label, status, duration_days,
                      nights_makkah, nights_madinah, departure_city, airline, description,
                      includes, excludes, requirements, booking_terms) VALUES
  ('eeeeeeee-0000-0000-0000-0000000000a1', 'eeeeeeee-0000-0000-0000-000000000001', 'UMR-MAWLID', 'umrah',
   'عمرة المولد النبوي - 15 يوم', 'ربيع الأول', 'active', 15, 10, 5, 'طرابلس', 'الخطوط الليبية',
   'برنامج اقتصادي مع فنادق قريبة ونقل مكيف',
   '["التأشيرة", "تذكرة الطيران ذهاب وعودة", "السكن", "النقل بين المدن", "زيارة المعالم في المدينة"]',
   '["الوجبات", "المصاريف الشخصية"]',
   'جواز سفر ساري لمدة 6 شهور على الأقل، صورتين شخصيتين بخلفية بيضاء، صورة من الرقم الوطني.',
   'عربون 30% عند التسجيل والباقي قبل السفر بأسبوعين.'),
  ('eeeeeeee-0000-0000-0000-0000000000a2', 'eeeeeeee-0000-0000-0000-000000000001', 'UMR-RAMADAN', 'umrah',
   'عمرة العشر الأواخر من رمضان - 12 يوم', 'رمضان', 'active', 12, 8, 4, 'طرابلس', 'الخطوط الليبية',
   'فنادق قريبة من الحرم مع السحور والإفطار',
   '["التأشيرة", "الطيران", "السكن", "السحور والإفطار"]', '[]',
   'جواز سفر ساري لمدة 6 شهور على الأقل، صورتين شخصيتين بخلفية بيضاء.',
   'عربون 30% عند التسجيل.'),
  ('eeeeeeee-0000-0000-0000-0000000000a3', 'eeeeeeee-0000-0000-0000-000000000001', 'HAJJ-VIP', 'hajj',
   'حج VIP - خيام قريبة في منى', '1448', 'active', 21, 14, 7, 'طرابلس', 'الخطوط الليبية',
   'برنامج حج متكامل', '["كل الخدمات"]', '[]',
   'التسجيل حسب شروط القرعة والجهات الرسمية. جواز ساري لمدة سنة.',
   'عربون 50% عند التسجيل.'),
  ('eeeeeeee-0000-0000-0000-0000000000a4', 'eeeeeeee-0000-0000-0000-000000000001', 'IST-DRAFT', 'tourism',
   'رحلة إسطنبول', NULL, 'draft', 7, 0, 0, 'طرابلس', NULL, NULL, '[]', '[]', NULL, NULL);

INSERT INTO package_aliases (tenant_id, package_id, alias) VALUES
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'عمرة المولد'),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'عمرة ربيع'),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a2', 'عمرة رمضان'),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a2', 'العشر الأواخر'),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a3', 'الحج');

INSERT INTO package_hotels (tenant_id, package_id, city, hotel_name, stars, distance_note, nights) VALUES
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'makkah',  'فندق النخبة (تجريبي)', 3, '700 متر من الحرم', 10),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'madinah', 'فندق الروضة (تجريبي)', 3, '400 متر من المسجد النبوي', 5),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a2', 'makkah',  'فندق الصفوة (تجريبي)', 5, '150 متر من الحرم', 8);

INSERT INTO package_departures (id, tenant_id, package_id, depart_date, return_date, registration_deadline, seats_total, seats_left, status) VALUES
  ('eeeeeeee-0000-0000-0000-0000000000d1', 'eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', current_date + 20, current_date + 34, current_date + 10, 45, 20, 'open'),
  ('eeeeeeee-0000-0000-0000-0000000000d2', 'eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', current_date + 40, current_date + 54, current_date + 30, 45, 3, 'few_left'),
  ('eeeeeeee-0000-0000-0000-0000000000d3', 'eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a2', current_date + 60, current_date + 71, current_date + 45, 40, 15, 'open'),
  ('eeeeeeee-0000-0000-0000-0000000000d4', 'eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a3', current_date + 200, current_date + 221, current_date + 150, 20, 2, 'few_left');

INSERT INTO package_prices (tenant_id, package_id, room_type, traveler_type, amount) VALUES
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'quad',   'adult',  6500),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'triple', 'adult',  7200),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'double', 'adult',  8400),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'quad',   'child',  5000),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a1', 'na',     'infant', 1500),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a2', 'quad',   'adult',  9500),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a2', 'triple', 'adult', 10500),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a2', 'double', 'adult', 12000),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a2', 'quad',   'child',  7000),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a3', 'quad',   'adult', 38000),
  ('eeeeeeee-0000-0000-0000-000000000001', 'eeeeeeee-0000-0000-0000-0000000000a3', 'double', 'adult', 45000);

COMMIT;
