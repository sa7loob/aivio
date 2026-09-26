from evals.checks import check_case


def test_passing_booking_case():
    expect = {"tools_all": ["create_lead"], "tool_args": {"create_lead": {"adults": 2}},
              "db_lead": {"adults": 2, "staff_notified": True}}
    calls = [("search_packages", {"query": "المولد"}, True),
             ("create_lead", {"adults": 1}, False),
             ("create_lead", {"adults": 2}, True)]
    failures, warnings = check_case(expect, calls, "تسجل طلبك", {"adults": 2}, 1)
    assert failures == [] and warnings == []


def test_failures_and_style_warnings():
    expect = {"tools_all": ["get_package_details"], "tools_none": ["create_lead"],
              "reply_contains_any": ["6500"], "reply_not_contains": ["مجاني"],
              "db_lead": {"adults": 2}}
    calls = [("create_lead", {}, True)]
    failures, warnings = check_case(expect, calls, "سوف نرسل لكم عرض مجاني", None)
    assert len(failures) == 5
    assert any("سوف" in w for w in warnings)
