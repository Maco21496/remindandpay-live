from api.app.services.sms_segments import estimate_sms_segments


def test_gsm_160_chars_single_segment():
    result = estimate_sms_segments("a" * 160)
    assert result.encoding == "gsm7"
    assert result.length_units == 160
    assert result.segments == 1


def test_gsm_161_chars_two_segments():
    result = estimate_sms_segments("a" * 161)
    assert result.encoding == "gsm7"
    assert result.length_units == 161
    assert result.segments == 2


def test_gsm_extension_counts_double_units():
    result = estimate_sms_segments("{" * 80)
    assert result.encoding == "gsm7"
    assert result.length_units == 160
    assert result.segments == 1

    result_over = estimate_sms_segments("{" * 81)
    assert result_over.encoding == "gsm7"
    assert result_over.length_units == 162
    assert result_over.segments == 2


def test_unicode_70_chars_single_segment():
    result = estimate_sms_segments("你" * 70)
    assert result.encoding == "ucs2"
    assert result.length_units == 70
    assert result.segments == 1


def test_unicode_71_chars_two_segments():
    result = estimate_sms_segments("你" * 71)
    assert result.encoding == "ucs2"
    assert result.length_units == 71
    assert result.segments == 2


def test_mixed_unicode_uses_ucs2():
    result = estimate_sms_segments("Hello🙂")
    assert result.encoding == "ucs2"
    assert result.length_units == len("Hello🙂")
    assert result.segments == 1
