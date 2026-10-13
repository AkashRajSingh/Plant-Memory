# Plant Memory evaluation report

## E1. Extraction against ground truth

| Field | Events | Coverage | Precision | Accuracy | Macro F1 | Outside taxonomy |
| --- | --- | --- | --- | --- | --- | --- |
| asset | 400 | 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| component | 391 | 0.987 | 0.974 | 0.962 | 0.953 | 0 |
| failure_mode | 350 | 0.991 | 0.896 | 0.889 | 0.88 | 7 |
| root_cause | 350 | 0.62 | 1.0 | 0.62 | 0.805 | 7 |
| action | 400 | 0.948 | 0.982 | 0.93 | 0.899 | 0 |
| symptom | 357 | 0.608 | 0.982 | 0.597 | 0.767 | 0 |

Failure vs. planned work: precision 0.989, recall 0.972.
Drift: 7 of 7 events with a failure mode outside the taxonomy were flagged for review.

Coverage is the share of events where a value was extracted; precision is accuracy on those. Root cause coverage is limited by design: many work orders carry no cause evidence at all, which is what the knowledge-gap interviews are for.

## E2. Diagnostic questions: raw SAP export vs. Plant Memory

28 questions of the form "asset X keeps showing failure mode Y; what is the most likely root cause?"

| Answerer | Top-1 correct | Top-3 correct | Answers with evidence |
| --- | --- | --- | --- |
| Raw SAP export (Root Cause column) | 0.536 | n/a | n/a |
| Plant Memory ontology | 0.821 | 0.964 | 1.0 |

| Asset | Failure mode | Events | True cause | Baseline | Plant Memory |
| --- | --- | --- | --- | --- | --- |
| P2R-BRN-CNV-01 | bearing_wear | 7 | inadequate_lubrication | no grease ✓ | inadequate_lubrication ✓ |
| P2R-BRN-CNV-01 | belt_wear | 13 | misalignment | stone in feed ✗ | misalignment ✓ |
| P2R-ELV-12 | belt_slippage | 7 | improper_tensioning | belt tension improper ✓ | improper_tensioning ✓ |
| P2R-ELV-12 | sprocket_tooth_wear | 7 | misalignment | misalignment ✓ | misalignment ✓ |
| P2R-HSK-01 | bearing_wear | 9 | inadequate_lubrication | life over ✗ | normal_wear ✗ |
| P2R-HSK-01 | rubber_roll_wear | 23 | normal_wear | life over ✓ | normal_wear ✓ |
| P2R-HSK-02 | belt_wear | 5 | normal_wear | wear & tear ✓ | normal_wear ✓ |
| P2R-HSK-02 | rubber_roll_wear | 24 | normal_wear | wear & tear ✓ | normal_wear ✓ |
| P2R-PLS-01 | bearing_seizure | 5 | moisture_ingress | lubrication failure ✗ | normal_wear ✗ |
| P2R-PLS-01 | screen_tear | 12 | normal_wear | foreign material ✗ | normal_wear ✓ |
| P2R-SRT-01 | valve_sticking | 8 | dust_ingress | life over ✗ | dust_ingress ✓ |
| P2R-WHT-01 | bearing_wear | 4 | inadequate_lubrication | (nothing recorded) ✗ | inadequate_lubrication ✓ |
| P2R-WHT-01 | screen_tear | 5 | foreign_material | (nothing recorded) ✗ | foreign_material ✓ |
| P2R-WHT-01 | stone_wear | 7 | normal_wear | stone in feed ✗ | foreign_material ✗ |
| PARB-DRY-ELV-01 | bearing_wear | 23 | misalignment | misalignment ✓ | misalignment ✓ |
| PARB-DRY-ELV-01 | bucket_damage | 6 | foreign_material | choke-up ✗ | foreign_material ✓ |
| PARB-DRY-ELV-01 | sprocket_tooth_wear | 10 | misalignment | misalignment ✓ | misalignment ✓ |
| PARB-DRYER-01 | sensor_failure | 7 | moisture_ingress | moisture ingress ✓ | moisture_ingress ✓ |
| PARB-ELV-01 | belt_slippage | 9 | improper_tensioning | normal wear ✗ | improper_tensioning ✓ |
| PARB-ELV-01 | sprocket_tooth_wear | 5 | misalignment | misalignment ✓ | misalignment ✓ |
| PARB-ELV-02 | sprocket_tooth_wear | 6 | normal_wear | misalignment ✗ | misalignment ✗ |
| PARB-HODI-CNV-01 | bearing_wear | 4 | dust_ingress | dust ingress ✓ | dust_ingress ✓ |
| PARB-HODI-CNV-01 | belt_wear | 12 | normal_wear | normal wear ✓ | normal_wear ✓ |
| PARB-HODI-ELV-01 | bearing_wear | 12 | inadequate_lubrication | life over ✗ | normal_wear ✗ |
| PARB-PCL-01 | screen_tear | 12 | foreign_material | life over ✗ | foreign_material ✓ |
| PCK-PKM-01 | bearing_wear | 5 | normal_wear | wear & tear ✓ | normal_wear ✓ |
| PCK-PKM-01 | electrical_fault | 6 | electrical_supply | single phasing ✓ | electrical_supply ✓ |
| UTL-DM-PMP-01 | seal_leakage | 4 | cavitation | dry running ✓ | cavitation ✓ |
