from app.channels.whatsapp import parse_webhook


def test_parses_messages(load_fixture):
    parsed = parse_webhook(load_fixture("whatsapp_messages.json"))

    assert [m.external_message_id for m in parsed.messages] == [
        "wamid.AAA1", "wamid.AAA2", "wamid.AAA3", "wamid.AAA4"]
    assert parsed.skipped == 1                      # الرسالة بدون id
    assert parsed.statuses == []

    first = parsed.messages[0]
    assert first.channel == "whatsapp"
    assert first.account_external_id == "PNID-NOOR-TEST"
    assert first.user_external_id == "218913334444"
    assert first.user_phone_e164 == "+218913334444"
    assert first.user_display_name == "أبو محمد"
    assert first.text == "السلام عليكم"
    assert first.timestamp.tzinfo is not None

    assert parsed.messages[1].text == "قداش عمرة رمضان؟"
    assert (parsed.messages[2].type, parsed.messages[2].text) == ("interactive", "نعم")
    assert (parsed.messages[3].type, parsed.messages[3].text) == ("image", "صورة الجواز")


def test_statuses_are_separated_from_messages(load_fixture):
    parsed = parse_webhook(load_fixture("whatsapp_statuses.json"))
    assert parsed.messages == []
    assert [s.status for s in parsed.statuses] == ["delivered", "failed"]
    assert parsed.statuses[1].errors[0]["code"] == 131047


def test_ignores_other_objects_and_garbage():
    assert parse_webhook({"object": "page", "entry": []}).messages == []
    assert parse_webhook({"object": "whatsapp_business_account"}).messages == []
    garbage = {"object": "whatsapp_business_account",
               "entry": [{"changes": [{"field": "messages", "value": {}}]},
                         {"changes": [{"field": "account_update", "value": {}}]}]}
    parsed = parse_webhook(garbage)
    assert parsed.messages == [] and parsed.skipped == 2
