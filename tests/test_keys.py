from datetime import date

from wotcha.store import keys


def test_partition_is_scoped_by_household():
    assert keys.hh_pk("demo") == "HH#demo"


def test_item_keys():
    assert keys.meal_key("demo", "tacos") == {"pk": "HH#demo", "sk": "MEAL#tacos"}
    assert keys.fence_key("demo") == {"pk": "HH#demo", "sk": "FENCE"}
    assert keys.member_key("demo", "alex") == {"pk": "HH#demo", "sk": "MEMBER#alex"}
    assert keys.week_key("demo", date(2026, 8, 24)) == {
        "pk": "HH#demo", "sk": "WEEK#2026-08-24"
    }


def test_signal_key_is_unique_per_person_meal_and_date():
    a = keys.signal_key("demo", date(2026, 8, 25), "maya", "tacos")
    b = keys.signal_key("demo", date(2026, 8, 25), "sam", "tacos")
    assert a != b
    assert a["sk"] == "SIGNAL#2026-08-25#maya#tacos"


def test_eval_keys_sort_chronologically():
    # unique values deliberately oppose timestamp order ("zzz" earlier,
    # "aaa" later) so this assertion can only hold if the timestamp -- not
    # the unique suffix -- is the component driving sort order.
    early = keys.eval_key("demo", "2026-08-20T10:00:00", "zzz")
    late = keys.eval_key("demo", "2026-08-20T11:00:00", "aaa")
    assert early["sk"] < late["sk"]


def test_drift_case_key():
    assert keys.drift_case_key("demo", "maya-chili") == {
        "pk": "HH#demo", "sk": "DRIFTCASE#maya-chili"
    }
