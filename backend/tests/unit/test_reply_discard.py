from datetime import datetime, timedelta, timezone

from app.worker.reply import should_discard

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def test_bot_mode_not_paused_sends():
    assert not should_discard("bot", None, False, NOW)
    assert not should_discard("bot", NOW - timedelta(minutes=1), False, NOW)   # إيقاف منتهي


def test_staff_takeover_discards():
    assert should_discard("human", None, False, NOW)
    assert should_discard("closed", None, False, NOW)
    assert should_discard("human", None, True, NOW)


def test_owner_replied_from_phone_discards():
    # echo من تطبيق واتساب للأعمال => bot_paused_until في المستقبل
    assert should_discard("bot", NOW + timedelta(hours=12), False, NOW)


def test_own_handoff_is_not_takeover():
    # handoff_to_human أوقف البوت في نفس التشغيل => رسالة التحويل للزبون تُرسل
    assert not should_discard("bot", NOW + timedelta(hours=12), True, NOW)
