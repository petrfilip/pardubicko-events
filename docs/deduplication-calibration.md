# Kalibrace deduplikace

Implementace je v `tools/pipeline/matching.py`; reprodukovatelný test v
`test_matching.py`. Tvrdý blok je `(date(start_at), municipality_id)`. Bez
normalizované obce se položka automaticky neporovnává a musí nejprve projít
normalizací nebo karanténou.

Skóre kombinuje Jaro-Winkler podobnost názvu (váha 0,70), času (0,15) a,
pokud jej uvádějí obě strany, místa (0,15). Chybějící místo se nepovažuje za
shodu ani neshodu; dostupné váhy se přepočtou. Pásma jsou:

- `>= 0.92`: automaticky propojit kandidáta s akcí a zachovat URL jako další
  `event_source`;
- `0.75–0.92`: uložit do `match_review`, nic automaticky neslučovat;
- `< 0.75`: samostatná položka, kterou následně řeší publish/kurátor.

První kalibrační korpus obsahuje sedm ručně očekávaných akcí z benchmarku,
doloženou Facebook duplicitu `fb-2412285059248213` /
`fb-4525215647723357` a negativní syntetický pár ve stejném čase. Všechny
dostupné pozitivní self-match případy i Facebook pár jsou v automatickém
pásmu, negativní pár je pod review prahem. Na tomto malém korpusu jsou **0
pozorovaných falešných sloučení**, což není odhad produkční chybovosti.

Prahy proto zůstávají konzervativní a před zvýšením automatického podílu se
musí report rozšířit o skutečně rozhodnuté řádky `match_review`, zejména o
opakovaná představení se stejným názvem ve stejný den. Kompromis záměrně
preferuje více ručních rozhodnutí před falešným sloučením.
