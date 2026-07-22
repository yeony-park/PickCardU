# OCR benchmark comparison

## Operational metrics (10 cards / 50 pages)

| Model | Success | Coverage | Empty | Duplicate | Schema | sec/page | $/page |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pymupdf | 1.000 | 0.860 | 0.140 | 0.120 | 1.000 | 0.124 | 0.000 |
| mistral | 1.000 | 1.000 | 0.000 | 0.080 | 1.000 | 1.308 | 0.004 |
| upstage | 1.000 | 1.000 | 0.000 | 0.080 | 1.000 | 1.990 | 0.010 |
| vision | 1.000 | 1.000 | 0.000 | - | - | - | - |

## BC key-number metrics (27 labels)

| Model | Numeric exact match | Relation context | Critical numeric exact match |
| --- | ---: | ---: | ---: |
| pymupdf | 0.889 | 0.741 | 0.778 |
| mistral | 0.926 | 0.741 | 1.000 |
| upstage | 0.963 | 0.815 | 1.000 |
| vision | 0.963 | 0.852 | 1.000 |

## BC page 2 text metrics

| Model | CER | WER | Numeric CER | Normalized edit distance |
| --- | ---: | ---: | ---: | ---: |
| pymupdf | 0.900 | 0.940 | 0.632 | 0.900 |
| mistral | 0.053 | 0.152 | 0.067 | 0.053 |
| upstage | 0.242 | 0.300 | 0.301 | 0.239 |
| vision | 0.045 | 0.143 | 0.024 | 0.045 |

## BC page 2 table metrics

| Model | TEDS | TEDS-S | Table detection recall |
| --- | ---: | ---: | ---: |
| pymupdf | 0.076 | 0.198 | 0.000 |
| mistral | 0.802 | 0.881 | 1.000 |
| upstage | 0.937 | 1.000 | 1.000 |
| vision | - | - | - |

## BC page 2 section order

| Model | Section coverage | Section-order accuracy |
| --- | ---: | ---: |
| pymupdf | 0.857 | 0.619 |
| mistral | 1.000 | 1.000 |
| upstage | 1.000 | 1.000 |
| vision | 1.000 | 1.000 |

Block F1, bbox IoU, block-level reading order, card-field extraction, and QA require additional ground-truth annotations.
