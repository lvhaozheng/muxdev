from scripts.verify_teaching_pack import verify


def test_teaching_pack_is_complete_and_grounded() -> None:
    assert verify() == []
