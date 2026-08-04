# Validace dat

Deterministická kontrola repozitáře. Nahrazuje strojovou část práce Quality
Agenta: co umí ověřit kód, nemá ověřovat model.

## Spuštění

```bash
docker compose run --rm validate      # kontrola dat
docker compose run --rm --build tests # Python, Node, PHP, HTTP smoke, validace a roundtrip
docker compose run --rm linkcheck     # dostupnost odkazů (síť)
```

Bez Dockeru:

```bash
pip install -r tools/validate/requirements.txt
python3 tools/validate/validate.py
```

Užitečné přepínače:

```bash
python3 tools/validate/validate.py --strict          # varování jako chyby
python3 tools/validate/validate.py --only weeks      # jen jedna skupina
python3 tools/validate/validate.py --json            # strojový výstup
```

`--only` vypíná sémantické kontroly, protože ty potřebují úplný obraz
repozitáře. Pro plnou kontrolu pouštěj validátor bez přepínače.

## Co se kontroluje

**Schéma** (`schemas/`) hlídá tvar jednotlivého souboru: povinná pole,
datové typy, povolené hodnoty, formát časových značek a tvar ID. Neznámé
pole je chyba — schéma je tím pádem i detektorem drifu mezi dokumentací
a tím, co agenti skutečně zapisují.

**Sémantické kontroly** (`checks.py`) hlídají vztahy:

- manifest odpovídá souborům na disku a ISO rozsahům týdnů a jeho
  `generated_at` není starší než `generated_at` žádného odkazovaného týdne,
- akce je zařazená do týdne, do kterého časově patří,
- `end_at >= start_at`,
- časový posun odpovídá Europe/Prague k danému datu,
- kopie jedné akce v různých týdnech mají shodná identitní pole (ADR 0001),
- dlouhá akce je zapsaná i v týdnu, ve kterém začíná,
- kandidát ve stavu `imported` odkazuje na existující produkční akci,
- slovník kategorií je vnitřně konzistentní: alias míří na existující
  kategorii, kategorie na existující osu, sporná hodnota říká totéž co
  mapovací tabulka,
- publikované akce mají pouze kanonická ID kategorií, bez duplicit, a
  alespoň jednu kategorii z každé povinné osy (dnes `kind`),
- registr zdrojů nemá duplicity a `config/sources.json` neexistuje,
- report běhu odpovídá svému názvu, umístění a časům.

**Varování** označují nálezy, které vyžadují úsudek, a proto neshazují CI:
homepage místo konkrétního detailu akce, různá ID se shodným zdrojem a
termínem, `source_id` mimo registr, `partial` bez důvodu a neznámá syrová
kategorie u kandidáta. Přísný režim (`--strict`) je povýší na chyby.

V `data/weeks/*.json` je nekanonická kategorie vždy chyba. Aliasová a
neznámá znění smějí existovat jen před publikační hranicí; adaptéry je
mapují nebo odkládají do měřeného `categories_unmapped`.

## Rozšíření

Nová kontrola patří do `checks.py` jako samostatná funkce vracející
`list[Finding]` a přidá se do `ALL_CHECKS`. Ke každé nové kontrole patří
test v `test_validate.py`, který ověří, že nad poškozeným vstupem skutečně
selže. Kontrola bez takového testu nemá důkaz, že něco hlídá.
