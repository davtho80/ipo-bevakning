from datetime import date

from app.sources.fi import parse_register, parse_timetable, share_prospectuses

REGISTER_HTML = """
<table class="grouped"><tbody>
<tr class="extended-click-area"><td class="date">2026-10-13</td><td>Exempel AB (publ)</td><td>Aktier</td>
<td><a href="/sv/vara-register/fis-marknadsregister/prospekt/register/details?id=26-1">Prospekt</a></td></tr>
<tr class="extended-click-area"><td class="date">2026-10-10</td><td>Banken AB</td>
<td>Skuldinstrument med ett nominellt v&#xE4;rde per enhet p&#xE5; minst 100 000 EUR</td>
<td><a href="/sv/vara-register/fis-marknadsregister/prospekt/register/details?id=26-2">Grundprospekt</a></td></tr>
<tr class="extended-click-area"><td class="date">2026-10-09</td><td>Noterat AB</td><td>Aktier</td>
<td><a href="/sv/vara-register/fis-marknadsregister/prospekt/register/details?id=26-3">EU-Uppföljningsprospekt</a></td></tr>
</tbody></table>"""


def test_parse_register_and_filter():
    rows = parse_register(REGISTER_HTML)
    assert [r.fi_id for r in rows] == ["26-1", "26-2", "26-3"]
    assert "värde" in rows[1].security
    shares = share_prospectuses(rows, date(2026, 11, 1), 60)
    assert [r.name for r in shares] == ["Exempel AB (publ)"]
    assert shares[0].pdf_url.endswith("GetFile?id=26-1")


TIMETABLE = """
Förväntad tidsplan för erbjudandet
Anmälningsperiod för allmänheten i Sverige, Danmark,
Norge och Finland

14 oktober 2026–
21 oktober 2026 klockan 16:30

Förväntad första handelsdag för Bolagets stamaktie
på Nasdaq First North Premier Growth Market

23 oktober 2026
Priset per stamaktie i Erbjudandet har fastställts till 31 SEK per stamaktie.
Enligt bolagsordningen ska antalet aktier vara lägst 15 000 000 aktier och högst 60 000 000 aktier.
Anmälan om förvärv av aktier ska avse lägst 100 aktier och därefter i jämna poster.
"""


def test_parse_timetable():
    r = parse_timetable(TIMETABLE, 2026)
    assert r["sub_start"] == "2026-10-14"
    assert r["sub_end"] == "2026-10-21T16:30"
    assert r["first_trading_day"] == "2026-10-23"
    assert r["price"] == 31.0 and r["currency"] == "SEK"
    assert r["min_subscription"] == "100 aktier"  # inte bolagsordningens 15 000 000
    assert r["market"] == "First North Premier"
    assert r["looks_like_rights_issue"] is False


def test_parse_timetable_dormy_style_and_range():
    text = ("Anmälningsperiod för allmänheten 16 september – 23 september 2026\n"
            "Första dag för handel i Bolagets aktier 24 september 2026\n"
            "Priset förväntas fastställas inom intervallet 60–70 SEK per aktie")
    r = parse_timetable(text, 2026)
    assert (r["sub_start"], r["sub_end"]) == ("2026-09-16", "2026-09-23")
    assert r["first_trading_day"] == "2026-09-24"
    assert (r["price_min"], r["price_max"]) == (60.0, 70.0)


def test_rights_issue_detected():
    text = "Teckningsperiod 1 december – 15 december 2026. " + "teckningsrätter " * 12
    assert parse_timetable(text, 2026)["looks_like_rights_issue"] is True
