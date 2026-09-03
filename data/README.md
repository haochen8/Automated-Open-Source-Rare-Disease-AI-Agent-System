# Restricted data setup

No challenge or patient data is distributed with this repository.

Authorized users must obtain the gated SageBio challenge dataset through the official challenge
process and store it **outside this Git checkout**, for example in a separately access-controlled
directory. Point the application to that location with
`RDA_PATHS__RESTRICTED_DATA_DIR=/absolute/private/path`.

Never commit or share patient VCF, BAM, CRAM, FASTQ, phenotype, pedigree, derived variant tables,
intermediate files, prompts, model outputs, tokens, or secrets. Do not place restricted data into
public issue reports or test fixtures. Tests in this repository use deliberately synthetic,
non-person data only.

Before committing, run:

```bash
python scripts/privacy_guard.py --all
```

The privacy guard and `.gitignore` reduce accidental disclosure risk but do not replace authorized
access controls, encryption, data-governance review, or the hackathon rules.
