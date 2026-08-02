"""Testy parseru nad řetězci skutečně odečtenými z Facebooku 2. 8. 2026.

Spuštění bez jakékoli závislosti:

    python3 tools/fb-events/test_parse.py
"""

from datetime import date

from parse import (
    ParseError,
    clean_municipality,
    clean_organizer,
    is_cohost_summary,
    parse_datetime_line,
    parse_listing_block,
    resolve_year,
    strip_cohost_summary,
)

TODAY = date(2026, 8, 2)
failures = []


def check(label, actual, expected):
    if actual != expected:
        failures.append(f"{label}\n    očekáváno: {expected!r}\n    dostal:    {actual!r}")


# --- datum a čas -------------------------------------------------------------

r = parse_datetime_line("Čt, 13. 8. v 20:30 CEST", today=TODAY)
check("letošní akce bez roku", r["start_at"], "2026-08-13T20:30:00+02:00")
check("letošní akce bez roku – konec", r["end_at"], None)
check("letošní akce bez roku – celodenní", r["all_day"], False)

r = parse_datetime_line("So, 29. 8. v 19:30 CEST", today=TODAY)
check("sobotní koncert", r["start_at"], "2026-08-29T19:30:00+02:00")

r = parse_datetime_line("Sobota 29. srpna 2026 v 14:00 CEST", today=TODAY)
check("měsíc slovem s rokem", r["start_at"], "2026-08-29T14:00:00+02:00")

r = parse_datetime_line("So, 7. 9. 2024", today=TODAY, prefer="past")
check("uplynulá akce s rokem", r["start_at"], "2024-09-07T00:00:00+02:00")
check("uplynulá akce je celodenní", r["all_day"], True)

r = parse_datetime_line("Pá, 1. 5.", today=TODAY, prefer="past")
check("uplynulá akce bez roku", r["start_at"], "2026-05-01T00:00:00+02:00")

# Leden ve výpisu nadcházejících akcí musí spadnout do příštího roku.
r = parse_datetime_line("Pá, 9. 1. v 19:00", today=TODAY)
check("přetočení přes Nový rok", r["start_at"], "2027-01-09T19:00:00+01:00")

# Rozsah přes půlnoc s uvedeným datem konce.
r = parse_datetime_line("30. 4. v 18:00 až 1. 5. v 5:00 CEST", today=date(2026, 4, 1))
check("rozsah přes půlnoc – začátek", r["start_at"], "2026-04-30T18:00:00+02:00")
check("rozsah přes půlnoc – konec", r["end_at"], "2026-05-01T05:00:00+02:00")

# Rozsah přes půlnoc bez data konce se musí posunout na další den, ne skončit dřív.
r = parse_datetime_line("So, 15. 8. v 22:00 až 3:00", today=TODAY)
check("konec po půlnoci bez data", r["end_at"], "2026-08-16T03:00:00+02:00")

# Vícedenní akce bez časů (formát z bloku „Navrhované události“).
r = parse_datetime_line("Pá, 7. 8. – 9. 8.", today=TODAY)
check("vícedenní bez času – začátek", r["start_at"], "2026-08-07T00:00:00+02:00")
check("vícedenní bez času – konec", r["end_at"], "2026-08-09T00:00:00+02:00")
check("vícedenní bez času je celodenní", r["all_day"], True)

# Nesmyslné vstupy musí spadnout, ne tiše vrátit dohad.
for bad in ("Zobrazit víc", "", "Událost vytvořena Divadlo 29", "v 20:30"):
    try:
        parse_datetime_line(bad, today=TODAY)
        failures.append(f"nevalidní vstup {bad!r} měl vyhodit ParseError")
    except ParseError:
        pass

check("rok pro srpen zůstává letošní", resolve_year(29, 8, TODAY), 2026)
check("rok pro leden se posouvá dopředu", resolve_year(9, 1, TODAY), 2027)
check("včerejšek se ještě počítá jako letošní", resolve_year(1, 8, TODAY), 2026)

# --- pomocné řádky -----------------------------------------------------------

check("pořadatel", clean_organizer("Událost vytvořena GAMPA - Galerie města Pardubic"),
      "GAMPA - Galerie města Pardubic")
check("pořadatel – jiná formulace", clean_organizer("Událost pořádá DrumCity"), "DrumCity")
check("řádek bez pořadatele", clean_organizer("Rozhledna Bára"), None)
check("obec", clean_municipality(" · Chrudim"), "Chrudim")
check("souhrn spolupořadatelů se pozná",
      is_cohost_summary("RockIn a TRAKTOR spolu s 4 dalšími"), True)
check("běžné jméno není souhrn", is_cohost_summary("Divadlo 29"), False)
check("odříznutí souhrnu", strip_cohost_summary("RockIn a TRAKTOR spolu s 4 dalšími"),
      "RockIn a TRAKTOR")
check("jméno se souhrnem se nemění", strip_cohost_summary("DrumCity"), "DrumCity")

# --- celý blok ---------------------------------------------------------------

block = parse_listing_block([
    "Pá, 14. 8. v 17:00 CEST",
    "Letní barokní scéna – Martin Málek, vernisáž výstavy Spojení v naději",
    "Museum of Baroque Chrudim Muzeum baroka",
    " ",
    " · Chrudim",
    "Událost vytvořena Chrudimská beseda, městské kulturní středisko",
], today=TODAY)
check("blok – začátek", block["start_at"], "2026-08-14T17:00:00+02:00")
check("blok – název", block["title"],
      "Letní barokní scéna – Martin Málek, vernisáž výstavy Spojení v naději")
check("blok – místo", block["venue"], "Museum of Baroque Chrudim Muzeum baroka")
check("blok – obec", block["municipality"], "Chrudim")
check("blok – pořadatel", block["organizers"],
      ["Chrudimská beseda, městské kulturní středisko"])

# Blok bez místa a obce (Facebook je běžně neuvádí).
block = parse_listing_block([
    "So, 15. 8. v 20:00 CEST",
    "Letní barokní scéna – Alessandro Melani: Il fratricidio di Caino",
    "Událost vytvořena Chrudimská beseda, městské kulturní středisko",
], today=TODAY)
check("neúplný blok – místo je None", block["venue"], None)
check("neúplný blok – obec je None", block["municipality"], None)

# Pomlčka v názvu nesmí rozbít parsování data na prvním řádku.
block = parse_listing_block([
    "Ne, 16. 8. v 20:00 CEST",
    "Letní barokní scéna – La Dafne",
    "Událost vytvořena Chrudimská beseda, městské kulturní středisko",
], today=TODAY)
check("pomlčka v názvu", block["title"], "Letní barokní scéna – La Dafne")
check("pomlčka v názvu – konec zůstává None", block["end_at"], None)

# Právě běžící akce: Facebook místo data píše „Právě probíhá“. Blok se nesmí
# zahodit ani se mu nesmí domyslet datum — termín doplní až detail.
block = parse_listing_block([
    "Právě probíhá",
    "Sportovní park Pardubice 2026 - slavíme 10 let!",
    "Park Na Špici",
    " · Pardubice",
    "Událost vytvořena Sportovní park Pardubice",
], today=TODAY)
check("probíhající akce – bez data", block["start_at"], None)
check("probíhající akce – příznak", block["ongoing"], True)
check("probíhající akce – date_text", block["date_text"], "Právě probíhá")
check("probíhající akce – název", block["title"], "Sportovní park Pardubice 2026 - slavíme 10 let!")
check("probíhající akce – místo", block["venue"], "Park Na Špici")
check("probíhající akce – obec", block["municipality"], "Pardubice")

# Opakovaná akce: počet termínů je v textu zdroje, nesmí se dopočítávat z odkazů.
block = parse_listing_block([
    "Út, 4. 8. ve 9:00 CEST a 23 dalších",
    "Za skřítky do Slatiňan",
    "Zámek Slatiňany",
    "Událost vytvořena Zámek Slatiňany",
], today=TODAY)
check("opakovaná akce – počet termínů", block["total_dates"], 24)
check("opakovaná akce – první termín", block["start_at"], "2026-08-04T09:00:00+02:00")
check("opakovaná akce – „ve 9:00“ se přečte", block["all_day"], False)
check("jednorázová akce nemá total_dates",
      parse_listing_block(["Čt, 13. 8. v 20:30 CEST", "Něco", "Událost vytvořena Kdosi"],
                          today=TODAY)["total_dates"], None)

check("běžná akce nemá příznak ongoing",
      parse_listing_block(["Čt, 13. 8. v 20:30 CEST", "Něco", "Událost vytvořena Kdosi"],
                          today=TODAY)["ongoing"], False)

# --- regrese z revize ---------------------------------------------------------

# Dlouhá akce, která už běží: rok začátku se musí odvodit od konce, ne samostatně.
# Dřív z toho vycházel začátek 2027 a konec 2026-09-01.
r = parse_datetime_line("1. 7. v 9:00 až 31. 8. v 17:00", today=TODAY)
check("dlouhý rozsah – začátek", r["start_at"], "2026-07-01T09:00:00+02:00")
check("dlouhý rozsah – konec", r["end_at"], "2026-08-31T17:00:00+02:00")

# Rozsah přes Nový rok: konec je v příštím roce, začátek musí zůstat v letošním.
r = parse_datetime_line("28. 12. v 18:00 až 2. 1. v 2:00", today=date(2026, 12, 1))
check("rozsah přes Nový rok – začátek", r["start_at"], "2026-12-28T18:00:00+01:00")
check("rozsah přes Nový rok – konec", r["end_at"], "2027-01-02T02:00:00+01:00")

# Neexistující datum musí být ParseError, ne holý ValueError, který shodí stránku.
for bad in ("Ne, 29. 2. v 20:00", "St, 31. 4. v 18:00", "Pá, 31. 11."):
    try:
        parse_datetime_line(bad, today=TODAY)
        failures.append(f"neexistující datum {bad!r} mělo vyhodit ParseError")
    except ParseError:
        pass
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{bad!r} vyhodilo {type(exc).__name__} místo ParseError")

# 29. 2. v přestupném roce existovat musí.
check("přestupný rok projde",
      parse_datetime_line("So, 29. 2. v 20:00", today=date(2028, 1, 1))["start_at"],
      "2028-02-29T20:00:00+01:00")

# Pomlčka bez mezer kolem se dřív nerozdělila a konec tiše mizel.
r = parse_datetime_line("Pá, 7. 8.–9. 8.", today=TODAY)
check("rozsah bez mezer kolem pomlčky", r["end_at"], "2026-08-09T00:00:00+02:00")

# Konec před začátkem při uvedeném datu konce je chyba zdroje, ne rozsah přes půlnoc.
try:
    parse_datetime_line("15. 8. v 20:00 až 10. 8. v 17:00", today=TODAY)
    failures.append("konec před začátkem měl vyhodit ParseError")
except ParseError:
    pass

# Probíhající akce s uvedeným koncem.
block = parse_listing_block([
    "Právě probíhá do 10. 8.",
    "Výstava něčeho",
    "Událost vytvořena Galerie",
], today=TODAY)
check("probíhající s koncem – ongoing", block["ongoing"], True)
check("probíhající s koncem – start zůstává None", block["start_at"], None)
check("probíhající s koncem – konec", block["end_at"], "2026-08-10T00:00:00+02:00")

if failures:
    print(f"NEPROŠLO {len(failures)} kontrol:\n")
    for f in failures:
        print(" - " + f)
    raise SystemExit(1)
print("Všechny kontroly prošly.")
