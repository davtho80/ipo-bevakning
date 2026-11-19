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

## Dina markeringar (steg 2)

- Snabbknappar på varje kort: **Intresserad**, **Tecknat**, **Ej intresserad**
  (tryck igen för att ta bort). "Ej intresserad" fälls ihop och läggs sist.
- **Ändra uppgifter** öppnar en sida där du anger tilldelning (antal aktier),
  teckningskurs och anteckning, samt **var du kan teckna**: "Finns på Avanza",
  "Inte på Avanza" och/eller annan mäklare/bank. Det styr markeringen
  "Kan teckna via Avanza" / "Kan teckna via annan mäklare: …" / "Okänt – kontrollera".
- Där kan du också rätta eller komplettera teckningsperiod (t.ex. mäklarens egen
  sista tid), pris, minsta teckning, första handelsdag och marknad. Egna uppgifter
  märks "Egen uppgift", går före källornas värden och skrivs aldrig över av hämtningen.
- Filtret **Mina** visar det du markerat som Intresserad eller Tecknat.

## Push-notiser (steg 3)

Du får en notis när

- **en ny IPO går att teckna i** – en känd teckningsperiod som är öppen eller
  ännu inte har börjat (också när en tidigare känd IPO får sin teckningsperiod),
- teckningen stänger inom **48 h** och inom **24 h** (högre prioritet).

Ingen notis för det du markerat **Ej intresserad**, och inga påminnelser för det
du markerat **Tecknat**. Varje notis skickas en gång; ändrar du sista
teckningstiden (t.ex. till mäklarens egen, tidigare tid) kommer nya påminnelser
för den tiden. Misslyckade utskick försöks igen vid nästa kontroll (var 15:e minut).

**Så kommer du igång**

1. Installera appen **ntfy** (App Store / Google Play).
2. Hitta på ett långt, slumpat ämnesnamn – på ntfy.sh fungerar ämnet som ett
   lösenord, alla som känner till det kan läsa notiserna. Till exempel:
   `python3 -c "import secrets; print('ipo-' + secrets.token_urlsafe(18))"`
3. Prenumerera på ämnet i ntfy-appen och lägg det i `.env`: `NTFY_TOPIC=...`
4. Valfritt: `APP_BASE_URL` (så att notisen öppnar rätt IPO) och `NTFY_TOKEN`
   om du skyddat ämnet med ett ntfy-konto.
5. Starta om appen och tryck **Skicka testnotis** längst ned på sidan.

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
till Avanza och Nordnet. Du kan själv markera "Kan teckna via Avanza"
eller ange annan mäklare (se ovan).

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
- `app/notify.py` – vilka notiser som ska skickas, och utskick via ntfy
- `app/web.py`, `app/templates/`, `app/static/` – gränssnittet

## Hosting (förslag)

Schemat måste köras även när datorn är av, och SQLite-filen ska finnas kvar –
alltså en liten server som alltid är på, inte en gratistjänst som somnar:

- **Hetzner Cloud CX22** (~4 €/mån) eller **Oracle Cloud Always Free** (gratis,
  men krångligare registrering). Kör appen med systemd, Caddy framför för HTTPS,
  och skydda sidan med lösenord (basic auth i Caddy) eftersom dina markeringar
  ligger där.
