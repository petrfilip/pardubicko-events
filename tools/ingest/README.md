# Manuální URL inbox

Inbox podle ADR 0005 zachytí odkaz bez commitu a bez běžícího serveru:

```bash
python3 tools/ingest/submit.py https://example.cz/akce/123 --note "tip z mobilu"
python3 tools/ingest/process.py
```

První příkaz pouze normalizuje URL a vloží ji do tabulky `inbox`. Druhý
použije společnou fetch/snapshot vrstvu, klasifikuje uložené HTML a provede
jeden z těchto přechodů:

- detail akce → `candidate` a kandidát `manual-submission` ve stavu `new`,
- kalendář nebo výpis → `source-proposal` bez změny registru zdrojů,
- nerozpoznaná stránka → `failed` s důvodem.

Chybu stažení lze zopakovat nejvýše třikrát. Záznam se automaticky nemaže.
Normalizovaná URL je unikátní, takže sledovací parametry nevytvoří duplicitu;
další vložení pouze doplní poznámku.

Databázi vytvoří nebo obnoví:

```bash
python3 tools/pipeline/pipeline.py import
```

Testy nepoužívají živou síť:

```bash
python3 tools/ingest/test_inbox.py
```
