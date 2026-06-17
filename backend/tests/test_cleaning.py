from app.ingestion.cleaning import (
    FLAG_MISSING,
    FLAG_PARSE_ERROR,
    FLAG_SENTINEL,
    FLAG_SUSPECT_OUTLIER,
    clean_numeric,
    clean_reverse_state,
    clean_rows,
    clean_timestamp,
    detect_outliers_iqr,
)


# --- clean_numeric -----------------------------------------------------------

def test_clean_numeric_valid():
    r = clean_numeric("51.02")
    assert r.value == 51.02 and r.flag is None


def test_clean_numeric_empty_is_missing():
    assert clean_numeric("").flag == FLAG_MISSING
    assert clean_numeric(None).flag == FLAG_MISSING


def test_clean_numeric_error_token_is_parse_error():
    assert clean_numeric("ERROR_TIMEOUT").flag == FLAG_PARSE_ERROR
    assert clean_numeric("NaN").flag == FLAG_PARSE_ERROR


def test_clean_numeric_sentinel():
    r = clean_numeric("-999")
    assert r.value is None and r.flag == FLAG_SENTINEL


def test_clean_numeric_negative_value_is_kept():
    # A legitimate negative steering angle is not a sentinel.
    r = clean_numeric("-26.9")
    assert r.value == -26.9 and r.flag is None


# --- clean_timestamp / clean_reverse_state -----------------------------------

def test_clean_timestamp_ddmmyyyy():
    ts = clean_timestamp("09/06/2026 10:00")
    assert ts is not None and ts.year == 2026 and ts.month == 6 and ts.day == 9


def test_clean_timestamp_bad_returns_none():
    assert clean_timestamp("not-a-date") is None


def test_clean_reverse_state():
    assert clean_reverse_state("1") is True
    assert clean_reverse_state("0") is False
    assert clean_reverse_state("") is None


# --- IQR outlier detection ---------------------------------------------------

def test_detect_outliers_iqr_flags_far_value():
    values = [50.0, 52.0, 51.0, 49.0, 53.0, 450.0]
    flags = detect_outliers_iqr(values)
    assert flags[-1] is True
    assert not any(flags[:-1])


def test_detect_outliers_ignores_none():
    values = [50.0, None, 51.0, 49.0, 53.0]
    flags = detect_outliers_iqr(values)
    assert not any(flags)


# --- end-to-end clean_rows over a representative slice -----------------------

def _row(ts, angle, speed, rev):
    return {"timestamp": ts, "wheel_angle": angle, "speed": speed, "reverse_state": rev}


def test_clean_rows_mixed_quality():
    raw = [
        _row("09/06/2026 10:00", "51.02", "51.02", "0"),
        _row("09/06/2026 10:00", "25.09", "", "0"),            # missing speed
        _row("09/06/2026 10:00", "23.88", "ERROR_TIMEOUT", "0"),  # parse error
        _row("09/06/2026 10:00", "-999", "53.02", "0"),        # sentinel angle
        _row("09/06/2026 10:00", "NaN", "63.97", "0"),         # NaN angle
    ]
    cleaned = clean_rows(raw)
    assert len(cleaned) == 5  # no row dropped

    assert cleaned[1].speed is None
    assert cleaned[1].quality_flags["speed"] == FLAG_MISSING

    assert cleaned[2].speed is None
    assert cleaned[2].quality_flags["speed"] == FLAG_PARSE_ERROR

    assert cleaned[3].wheel_angle is None
    assert cleaned[3].quality_flags["wheel_angle"] == FLAG_SENTINEL

    assert cleaned[4].wheel_angle is None
    assert cleaned[4].quality_flags["wheel_angle"] == FLAG_PARSE_ERROR


def test_clean_rows_keeps_outlier_value_with_flag():
    # A cluster around 50-55 plus one extreme 450 -> kept but flagged.
    raw = [_row("09/06/2026 10:00", "10.0", str(s), "0")
           for s in [50, 52, 51, 49, 53, 54, 48, 450]]
    cleaned = clean_rows(raw)
    outlier = cleaned[-1]
    assert outlier.speed == 450.0  # value preserved
    assert outlier.quality_flags["speed"] == FLAG_SUSPECT_OUTLIER


def test_outlier_detection_is_regime_aware():
    # Bimodal by reverse_state: slow reverse creep (~10-12) + fast forward
    # (~50-65). Low reverse speeds are legitimate and must NOT be flagged;
    # only the true spikes (125 in reverse, 450 in forward) should be.
    forward = [50, 52, 51, 49, 53, 54, 48, 55, 52, 51, 450]
    reverse = [11, 12, 10, 11, 12, 10, 11, 12, 125]
    raw = [_row("09/06/2026 10:00", "5.0", str(s), "0") for s in forward]
    raw += [_row("09/06/2026 10:00", "5.0", str(s), "1") for s in reverse]
    cleaned = clean_rows(raw)

    flagged = {
        c.speed for c in cleaned
        if c.quality_flags.get("speed") == FLAG_SUSPECT_OUTLIER
    }
    assert flagged == {450.0, 125.0}
    # No legitimate slow-reverse speed got flagged.
    for c in cleaned:
        if c.reverse_state and c.speed in (10.0, 11.0, 12.0):
            assert "speed" not in c.quality_flags
