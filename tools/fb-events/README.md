# tools/fb-events

Deterministický sběr veřejných událostí z Facebooku. Produkuje kandidáty pro Curator Agenta, nikdy nezapisuje do `data/weeks/`.

Pravidla kanálu, jeho rozsah a omezení popisuje `docs/agents/facebook-agent.md`. Tady je jen provozní návod.

## Instalace

Nástroj je oddělený od webu — statická stránka zůstává bez jakýchkoli závislostí a v kořeni repozitáře záměrně není `package.json`, aby GitHub Pages nepředpokládaly build.

```bash
python3 -m venv .venv
.venv/bin/pip install -r tools/fb-events/requirements.txt
.venv/bin/playwright install chromium
```

## Použití

```bash
.venv/bin/python tools/fb-events/fb_events.py                      # plný běh
.venv/bin/python tools/fb-events/fb_events.py --dry-run            # nic nezapíše
.venv/bin/python tools/fb-events/fb_events.py --limit-pages 3      # jen první tři stránky
.venv/bin/python tools/fb-events/fb_events.py --pages divadlo29    # jen vybraný zdroj
.venv/bin/python tools/fb-events/fb_events.py --no-detail          # bez detailů, rychlé
.venv/bin/python tools/fb-events/fb_events.py --ignore-known       # nepřeskakovat známé akce
```

`--ignore-known` vypne deduplikaci proti `research/` a `data/weeks/`. Používej ho při ladění parsování nebo když potřebuješ obnovit pole u už zachycených akcí; při běžném běhu ne, zbytečně zatěžuje platformu i backlog.

Vstupem je `config/facebook-sources.json`. Výstupem jsou dva soubory:

- `research/candidates-YYYY-MM-DD-facebook.json` — kandidáti, všichni ve stavu `needs-verification`
- `stats/runs/YYYY-MM/YYYY-MM-DD-HHMM-facebook.json` — report běhu podle `docs/monitoring.md`

### Časování a stránkování

Výchozí hodnoty jsou nastavené podle měření, ne od oka. Neměň je bez důvodu:

- `--render-wait 8` — čekání na vykreslení výpisu. Při 3,5 s vracelo Muzeum východních Čech nula akcí, při 8 s šest. Kratší hodnota vyrobí falešně prázdnou stránku.
- `--max-scrolls 8` — Facebook zobrazí bez scrollování jen 8 akcí. Bez dorolování by se výpis tiše ořezal. Když ani osm dorolování nestačí, nástroj to napíše do reportu a poradí hodnotu zvýšit.
- `--detail-wait 4` — detail akce se vykresluje rychleji než výpis.
- `--delay 3` — pauza mezi requesty, nelze snížit pod 1 s.

## Testy

Parser má testy nad řetězci skutečně odečtenými z Facebooku. Běží bez závislostí a bez sítě:

```bash
cd tools/fb-events && python3 test_parse.py
```

Po každé změně parsování je spusť. Když Facebook změní formát data, projeví se to nejdřív tady.

## Co nástroj dělá a co ne

Dělá: načte veřejné seznamy událostí, vytáhne datum, čas, název, místo, pořadatele a adresu, odstraní duplicity podle `facebook_event_id`, posbírá ID z bloku „Navrhované události“ a zapíše report.

Nedělá: nepřiřazuje kategorie, neurčuje skutečnou obec, nedohledává vstupné, nespojuje duplicity a nerozhoduje o zařazení do produkčních dat. To všechno je práce Curatora.

## Ohleduplnost

V kódu je natvrdo: žádné přihlašování, žádné podvržení prohlížeče, sekvenční průchod, `--delay` nelze snížit pod 1 s. Výchozí pauza jsou 3 s mezi requesty.

## Údržba

Facebook stránky nemají JSON-LD, takže se parsuje viditelný text. Změna UI kanál rozbije. Hlídací mechanismus je metrika `facebook_blocked` v reportu — pokud vyskočí nad nulu u stránek, které dřív fungovaly, změnil se formát. Prázdný výsledek při `facebook_blocked: 0` naopak znamená, že stránka opravdu nemá nadcházející akce, což je u malých pořadatelů běžné.
