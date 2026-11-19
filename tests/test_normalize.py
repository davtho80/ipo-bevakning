from datetime import date

from app.normalize import company_key, detect_market, parse_sv_date, same_company


def test_company_key_strips_legal_forms():
    assert company_key("Prisjakt AB (publ)") == company_key("Prisjakt")
    assert company_key("Reaktor Group Oyj") == "reaktor group"


def test_same_company_contiguous_tokens():
    assert same_company("Dormy", "Dormy Corporation AB")
    assert same_company("Public Property Invest", "PPI Public Property Invest AB (publ)")
    assert not same_company("Nordic", "Nordic Paper Holding")  # för kort ensamt ord
    assert not same_company("Salvos", "Savox")


def test_parse_sv_date():
    assert parse_sv_date("21 oktober 2026") == date(2026, 10, 21)
    assert parse_sv_date("14 oktober", 2026) == date(2026, 10, 14)
    assert parse_sv_date("ingen datum") is None


def test_detect_market_prefers_premier():
    text = "Nasdaq First North Premier Growth Market ... Nasdaq First North Premier ... First North"
    assert detect_market(text)[0] == "First North Premier"
    assert detect_market("Spotlight Stock Market och Spotlight") == ("Spotlight", "SE")
