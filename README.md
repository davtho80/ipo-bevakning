# IPO-bevakning Norden

Håller koll på kommande nordiska börsintroduktioner och visar teckningsperiod,
pris, minsta teckning, första handelsdag, prospekt och källa – med nedräkning
och filtret **Kan teckna nu**. Bara fakta, inga investeringsråd. Ingen AI eller
betal-API vid körning.

## Kom igång (lokalt)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # justera vid behov
.venv/bin/python -m app.fetch # första hämtningen (ca 1 min)
.venv/bin/uvicorn app.web:app --port 8000
```

Öppna http://localhost:8000. Med `IPO_SCHEDULER=1` hämtar appen själv
kl. 07:05 och 16:05 (ändras med `IPO_FETCH_TIMES`). Kräver `pdftotext`
(paketet `poppler-utils`) för att läsa prospekt.

Tester: `.venv/bin/python -m pytest -q`

## Källor – vad som används och varför

| Källa | Används | Villkor |
|---|---|---|
| Inderes IPO-kalender | Ja: bolag, handelsdag, pris, valuta (alla nordiska länder) | Personligt, icke-kommersiellt bruk med källangivelse; robots.txt tillåter |
| Finansinspektionens prospektregister | Ja: aktieprospekt + tolkning av tidsplanen i PDF:en (teckningsperiod med klockslag, handelsdag, pris, minsta teckning, marknad) | Öppna data, fritt att vidareutnyttja med källa FI |
| Avanza | **Nej** – användarvillkoren förbjuder robotar/scrapers | – |
| Nordnet | **Nej** – villkoren förbjuder maskinell inhämtning | – |
| MFN | **Nej** – robots.txt spärrar alla flöden (rss/xml/json) | – |
| Levler | Ej relevant – Levler erbjuder inte teckning i IPO:er, bara köp från första handelsdag | – |

Därför visas mäklartillgänglighet som **"Okänt – kontrollera"** med direktlänkar
till Avanza och Nordnet. I steg 2 kan du själv markera "Kan teckna via Avanza"
eller ange annan mäklare.

Begränsningar: FI täcker bara svenska prospekt, och små noteringar på First
North/Spotlight/NGM görs ofta utan prospekt (bara memorandum) – då finns bara
Inderes uppgifter. Tider tolkade ur prospekt markeras med källa; mäklare
stänger ofta tidigare än prospektets sista dag.

## Uppbyggnad

- `app/sources/` – en modul per källa (`inderes.py`, `fi.py`) + artig HTTP-klient
  (egen User-Agent, robots.txt, 2 s mellan anrop, endast godkända värdar)
- `app/fetch.py` – hämtar, slår ihop dubbletter (normaliserade bolagsnamn), sparar
- `app/db.py` – SQLite; varje uppgift sparas med källa, visat värde väljs per fält
  efter prioritet (egen uppgift > prospekt > Inderes, men Inderes före prospekt för
  pris och handelsdag eftersom Inderes uppdateras efter prissättning)
- `app/view.py` – status, nedräkning, filter
- `app/web.py`, `app/templates/`, `app/static/` – gränssnittet

## Hosting (förslag)

Schemat måste köras även när datorn är av, och SQLite-filen ska finnas kvar –
alltså en liten server som alltid är på, inte en gratistjänst som somnar:

- **Hetzner Cloud CX22** (~4 €/mån) eller **Oracle Cloud Always Free** (gratis,
  men krångligare registrering). Kör appen med systemd, Caddy framför för HTTPS,
  och skydda sidan med lösenord (basic auth i Caddy) eftersom dina markeringar
  ligger där.
