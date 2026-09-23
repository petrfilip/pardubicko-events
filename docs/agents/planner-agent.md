# Planner Agent

> **Zrušeno od 23. 9. 2026 (ADR 0008).** Planner připravoval
> `research/daily-plan.json` pro Discovery a ten soubor je spolu se zbytkem
> `research/` zmrazený. Všechno, z čeho plán vycházel, dnes vydává API:
> splatné zdroje (`klient sources list --due`), tlak backlogu
> (`klient candidates list` → `total`), publikované akce (`klient events find`)
> a číselník obcí (`klient taxonomy`). Discovery si proto plán sestavuje sám na
> začátku běhu podle pravidel v `docs/agents/discovery-agent.md`, oddíl
> *Vstupy a plán běhu*; platí tam i dřívější prahy tlaku backlogu a limity
> z `config/discovery-policy.json`.
>
> Deterministické zdroje s adaptérem plánuje server podle
> `check_interval_days`; pipeline je stahuje každý den v NanoClaw.

Historické znění role je v gitu (`git log -- docs/agents/planner-agent.md`).
