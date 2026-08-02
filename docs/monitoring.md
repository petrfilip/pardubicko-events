# Monitoring agentních běhů

Tento dokument popisuje, jak projekt měří výkon Planner, Discovery, Curator a Quality Agentů.

## Umístění reportů

Každý běh uloží samostatný JSON soubor:

`stats/runs/YYYY-MM/YYYY-MM-DD-HHMM-<agent>.json`

Příklady:

- `stats/runs/2026-08/2026-08-02-0530-planner.json`
- `stats/runs/2026-08/2026-08-02-0600-discovery.json`
- `stats/runs/2026-08/2026-08-02-0800-curator.json`
- `stats/runs/2026-08/2026-08-02-1800-quality.json`

Každý běh má vlastní soubor a nepřepisuje historii.

Pokud by v jedné minutě vznikly dva reporty stejného agenta, použij v názvu pozdějšího reportu přesnější čas nebo jednoznačný suffix. Existující report nikdy nepřepisuj.

## Společný formát

```json
{
  "schema_version": 1,
  "agent": "discovery",
  "run_id": "2026-08-02-0600-discovery",
  "started_at": "2026-08-02T06:00:00+02:00",
  "finished_at": "2026-08-02T06:18:00+02:00",
  "duration_seconds": 1080,
  "status": "success",
  "partial_reason": null,
  "commit_sha": null,
  "metrics": {},
  "coverage": {},
  "errors": [],
  "notes": []
}
```

Povolené stavy:

- `success`
- `partial`
- `failed`
- `no-change`

Pokud přesnou dobu běhu nelze změřit, použije agent `duration_seconds: null`. Čas ani jiné metriky se nesmějí odhadovat.

## Planner metriky

- `municipalities_planned`
- `sources_planned`
- `districts_planned`
- `regions_planned`
- `query_budget`
- `candidate_limit`
- `backlog_before`
- `backlog_pressure`

## Discovery metriky

- `municipalities_checked`
- `sources_checked`
- `queries_executed`
- `candidates_found`
- `unique_candidates`
- `duplicate_candidates`
- `new_sources_found`
- `google_candidates`
- `facebook_candidates`
- `kudyznudy_candidates`
- `known_source_candidates`

## Facebook metriky

Kanál veřejných Facebook stránek je specializovaný discovery průchod. Jeho report používá společný formát a doplňuje vlastní metriky:

- `facebook_pages_checked`
- `facebook_events_extracted`
- `facebook_details_fetched`
- `facebook_blocked`
- `facebook_duplicates`
- `facebook_suggested_events_seen`

Význam:

- `facebook_pages_checked` — počet veřejných stránek, jejichž seznam událostí byl skutečně načten. Stránka, která se nenačetla, se sem nepočítá.
- `facebook_events_extracted` — počet událostí vytěžených ze seznamů, před deduplikací.
- `facebook_details_fetched` — počet otevřených detailů událostí. Detail se otevírá až po deduplikaci, takže tato hodnota bývá výrazně nižší než `facebook_events_extracted`.
- `facebook_blocked` — počet stránek, ze kterých se nepodařilo vytěžit očekávanou strukturu.
- `facebook_blocks_unparsed` — počet jednotlivých bloků výpisu, které se nepodařilo naparsovat, přestože zbytek stránky prošel. Jsou to potenciálně ztracené akce. Metrika vznikla poté, co se ukázalo, že Facebook u právě běžících akcí místo data píše „Právě probíhá“ a takové akce tiše propadaly. Nenulová hodnota znamená neznámý formát, ne prázdnou stránku — vždy ji prověř na konkrétní stránce.
- `facebook_duplicates` — počet vytěžených událostí zahozených jako duplicita podle `facebook_event_id` nebo podle shody s existujícím kandidátem či produkční akcí.
- `facebook_suggested_events_seen` — počet akcí viděných v bloku „Navrhované události“ na detailech. Měří přínos tohoto discovery vektoru za hranice seed listu. Report k tomu nese pole `suggested_event_ids` s úplným seznamem ID; seznam se nikdy neořezává, aby se z něj dal sestavit další běh.

`facebook_blocked` není běžná chyba načtení. Stránky nemají JSON-LD a parsuje se z nich viditelný text, takže tato metrika je hlavní detektor dvou různých problémů: změny UI Facebooku, která rozbila parsování, nebo omezení ze strany platformy. V obou případech kanál tiše přestane vracet akce, aniž by cokoli spadlo — proto se nenulová hodnota vždy uvádí v `errors` a běh se označí nejméně jako `partial`.

Nulová výtěžnost sama o sobě chyba není. Malí venkovští pořadatelé Facebook události nezakládají, takže očekávaná výtěžnost je zhruba 3,5 akce na městskou kulturní instituci a blízká nule u obcí. Rozlišuj proto `facebook_blocked` (technický problém) od nulového `facebook_events_extracted` při úspěšně načtené stránce (běžný a očekávaný stav).

## Curator metriky

- `candidates_reviewed`
- `events_added`
- `events_updated`
- `events_cancelled`
- `candidates_rejected`
- `duplicates_removed`
- `deferred_candidates`
- `backlog_after`

`backlog_after` je počet unikátních kandidátů napříč všemi `research/candidates*.json`, jejichž stav je po běhu `new`, `needs-verification` nebo přechodový starší stav `verified`. Stavy `imported` a `rejected` jsou uzavřené.

## Quality metriky

- `json_files_checked`
- `events_checked`
- `broken_links_found`
- `generic_links_found`
- `duplicate_events_found`
- `schema_errors_found`
- `issues_fixed`
- `issues_deferred`
- `pages_check`

## Pokrytí

Sekce `coverage` má obsahovat jen údaje skutečně zkontrolované v daném běhu:

```json
{
  "regions": ["pardubicky-kraj", "kralovehradecky-kraj"],
  "districts": ["Chrudim", "Pardubice", "Hradec Králové"],
  "municipalities": ["Pardubice", "Hlinsko", "Hradec Králové"]
}
```

U Planner Agenta obsahuje `coverage` naplánované lokality. U Discovery a Curator Agenta obsahuje pouze lokality, jejichž zdroj nebo kandidát byl skutečně otevřen a zpracován.

## Udržitelnost

- Jeden běh vytváří pouze jeden kompaktní report.
- Podrobné logy se neukládají, pokud nejsou potřebné k řešení chyby.
- Reporty se po měsících archivují v samostatných adresářích.
- Souhrnné ukazatele se mají vypočítávat z reportů, ne ručně kopírovat do každého souboru.
- Monitoring nesmí výrazně zvyšovat počet dotazů ani velikost kurátorského backlogu.

## Odvozené ukazatele

Později lze z historie vypočítat například:

- potvrzovací poměr kandidátů,
- počet nových akcí na dotaz,
- počet nových akcí na zkontrolovaný zdroj,
- podíl duplicit,
- počet akcí podle obce a okresu,
- dobu od poslední kontroly obce,
- vývoj backlogu,
- úspěšnost jednotlivých discovery kanálů.

Tyto ukazatele nejsou důkazem správnosti konkrétní akce. Slouží pouze k plánování a hodnocení provozu.
