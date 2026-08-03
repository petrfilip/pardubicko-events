# Poznatky o zdrojích

Sdílená znalostní báze agentů. Zapisuje sem Discovery, Curator i Quality Agent
poznatky, které by příští běh jinak musel objevovat znovu.

## Pravidla zápisu

- Zapisuj jen **doložené pozorování**, ne dojem ani domněnku.
- U každého záznamu uveď, odkud poznatek pochází: konkrétní akce, běh, nebo soubor.
- Poznatek, který přestal platit, oprav nebo označ jako neplatný. Nemaž historii.
- Poznatky o jednom zdroji drž pohromadě.
- Provozní metriky sem nepatří; ty jsou v `stats/`.

Formát záznamu:

```
### <zdroj nebo téma>

- **<pozorování>** — <důsledek pro práci agenta>. Doloženo: <odkaz na akci, běh či soubor>.
```

---

## Tvar zdrojových odkazů

### ustinadorlici.cz — stránkovaný kalendář

- **Kalendář akcí používá v URL parametr stránkování** (`?cal_limitstart=110`). Odkaz dnes funguje, ale jak přibývají akce, stránkování se posune a odkaz povede jinam. Nepoužívej jej jako `source.url`; dohledej detail akce. Doloženo: `stredecni-pohadka-knihovna-usti-nad-orlici-2026-08-12` v `data/weeks/2026-W33.json`.

### zpfestival.cz — homepage místo detailu

- **Pořadatel měl program jen na homepage a u zahájení uváděl „Detail připravujeme“.** U akce `zlata-pecka-zahajeni-2026-08-23` byl proto 3. 8. 2026 nahrazen obecný odkaz konkrétním regionálním článkem, který uvádí datum, čas, místo i program. Při další kontrole preferuj nový oficiální detail, pokud ho festival doplní. Doloženo: `data/weeks/2026-W34.json` a program `https://www.zpfestival.cz/` ověřený 3. 8. 2026.

## Facebook kanál

Podrobná provozní pravidla jsou v `docs/agents/facebook-agent.md`. Zde jen
poznatky, které mění chování ostatních agentů.

- **Geoznačka stránky neurčuje obec konání.** Obec urči podle skutečného místa a ověř na primárním zdroji. Doloženo: `fb-1604923597719412`, prohlídka ve Výstavní síni Chrudim měla geoznačku `Kočí`.
- **U právě probíhajících akcí Facebook místo data píše „Právě probíhá“.** Termín je až v detailu a bývá nepřesný zejména v konci akce. Doloženo: metrika `facebook_blocks_unparsed`, kvůli tomuto jevu zavedená.
- **Pořadatelé zakládají tutéž akci dvakrát pod různými ID.** Rozliš dvojí založení od dvou skutečných představení ve stejný čas. Doloženo: `fb-2412285059248213` a `fb-4525215647723357`.
- **Shoda podle `facebook_event_id` nestačí k deduplikaci.** V dávce z 2. 8. 2026 se s produkčními daty krylo 6 z 82 kandidátů, přestože podle `facebook_event_id` neodpovídal ani jeden.
- **Nulová výtěžnost u obcí je normální stav.** Malí venkovští pořadatelé události na Facebooku nezakládají. Očekávaná výtěžnost je zhruba 3,5 akce na městskou kulturní instituci a blízká nule u obcí.

## Konzistence registru zdrojů

- **Čtyři facebookové stránky dříve odkazovaly na `source_id`, který v registru chyběl**: `divadlo-29`, `divadlo-karla-pippicha`, `letos-rychtarem`, `tic-hlinsko`. Dne 3. 8. 2026 byly po ověření jejich oficiálních webů do `config/source-registry.json` doplněny. Doloženo: registr a původní nález validátoru z 2. 8. 2026.

## Zápis identifikátorů akcí

- **Kopie dlouhodobé akce nesmí mít ID rozlišené týdnem.** Objevil se návyk připojovat k ID sufix `-w31`, `-w33`. ADR 0001 vyžaduje u všech kopií shodné ID; liší se pouze pole `week`. Doloženo: opravené `bienale-ve-veci-umeni-nezbytna-prani-2026-w33` a dosud neuzavřený rozpor u `sportovni-park-pardubice-2026`.
