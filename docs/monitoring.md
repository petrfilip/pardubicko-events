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

## Curator metriky

- `candidates_reviewed`
- `events_added`
- `events_updated`
- `events_cancelled`
- `candidates_rejected`
- `duplicates_removed`
- `deferred_candidates`
- `backlog_after`

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
