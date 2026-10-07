# Shop standards

Every value the engine and the rules use comes from the YAML tables in this folder.
The AI never supplies these numbers.

**Tables marked `placeholder: true` are not shop values.** They are typical
textbook-style figures put in so the code can run. Replace every row with your own
standards, set the source to your document and section, and delete the
`placeholder: true` line. `pytest -m needs_shop_standards` fails until no
placeholder table is left.

| File | Table | Used by |
| --- | --- | --- |
| materials.yaml | materials | cutting force, rules |
| clearance.yaml | clearance_pct_by_material | rule PT-CLR-001 |
| min_punch_diameter.yaml | min_punch_diameter | rule PT-MIN-004 |
| min_distances.yaml | min_web_and_edge | rules PT-WEB-001, PT-EDGE-001 |
| press_factors.yaml | press_factors | stripping force, press tonnage |
| strip_allowances.yaml | strip_allowances | strip layout bridge and edge allowance |
| rules/*.yaml | (rules) | rules engine |
