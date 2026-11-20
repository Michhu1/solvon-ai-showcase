import pytest

from claims_assistant.pii import fingerprint, is_valid_pesel, mask_pii


def test_valid_pesel_is_masked():
    masked, found = mask_pii("My PESEL is 44051401359.")
    assert masked == "My PESEL is [PESEL]."
    assert found == ["PESEL"]


def test_eleven_digits_with_a_wrong_checksum_are_not_a_pesel():
    assert not is_valid_pesel("44051401358")
    assert mask_pii("Order 44051401358")[0] == "Order 44051401358"


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("write to jan.k@example.com", "EMAIL"),
        ("call +48 601 234 567", "PHONE"),
        ("call 601-234-567", "PHONE"),
        ("policy MOT-2026-004512", "POLICY_NO"),
        ("account PL61 1090 1014 0000 0712 1981 2874", "IBAN"),
    ],
)
def test_masks_other_identifiers(text, label):
    masked, found = mask_pii(text)
    assert f"[{label}]" in masked
    assert found == [label]


def test_amounts_and_dates_survive_masking():
    text = "On 14.11.2026 the repair cost 3 850 PLN."
    assert mask_pii(text) == (text, [])


def test_fingerprint_is_stable_and_does_not_contain_the_input():
    assert fingerprint("PESEL 44051401359") == fingerprint("PESEL 44051401359")
    assert "44051401359" not in fingerprint("PESEL 44051401359")


@pytest.mark.parametrize("text", ["601234567", "+48601234567", "+48 601-234-567"])
def test_phone_variants(text):
    assert mask_pii(f"tel {text}")[0] == "tel [PHONE]"


def test_domestic_account_number_without_country_code():
    assert mask_pii("konto 61 1090 1014 0000 0712 1981 2874")[0] == "konto [IBAN]"
