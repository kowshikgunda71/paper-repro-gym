# Free & government data for reproductions

A paper is far easier — and more lawful — to reproduce when its data is public.
Prefer reproducing papers that use **open / government datasets**: acquisition
becomes a simple, allow-listed download, and there is no licence entanglement.

## Why this matters for reproduction

`gym acquire` downloads only from domains you allow-list, checksum-verifies, and
scans. Government and open-data hosts are ideal for that: stable URLs, permissive
terms, no login. When you scaffold an experiment, put these hosts in
`experiment.json`'s `allowed_domains`.

## US government open data (huge, ML-ready, free)

- **[data.gov](https://data.gov/)** — 300k+ US federal datasets, one search.
- **[NASA](https://data.nasa.gov/) / [Earthdata](https://www.earthdata.nasa.gov/)** — earth science, imagery, climate; also contributes AI-ready datasets to NAIRR.
- **[NOAA](https://www.noaa.gov/data)** — weather/climate, one of the world's largest data producers (Registry of Open Data mirrors many sets).
- **[NIH / NCBI](https://www.ncbi.nlm.nih.gov/)** — biomedical: GenBank, PubMed, SRA; open + (controlled) dbGaP. NIH co-leads NAIRR Secure for health AI.
- **[US Census](https://www.census.gov/data.html)** — demographic/economic.
- **[USGS](https://www.usgs.gov/products/data)** — geology, Landsat/satellite imagery.
- **[NIST](https://www.nist.gov/data)** — reference datasets and standards.

## International government / intergovernmental

- **[data.europa.eu](https://data.europa.eu/)** — EU Open Data Portal.
- **[data.gov.uk](https://www.data.gov.uk/)** — UK government data.
- **[World Bank Open Data](https://data.worldbank.org/)**, **[UN Data](https://data.un.org/)**, **[WHO](https://www.who.int/data)**.
- **[CERN Open Data](https://opendata.cern.ch/)** — particle-physics datasets + software.

## Cloud-hosted public data (free egress within that cloud)

If you run on a cloud's free tier, its public-data program often gives free,
fast access to the same government sets:

- **[AWS Registry of Open Data](https://registry.opendata.aws/)**
- **[Google Cloud Public Datasets](https://cloud.google.com/datasets)**
- **[Azure Open Datasets](https://learn.microsoft.com/azure/open-datasets/dataset-catalog)**

## Government compute that *also* bundles data + models

- **[NAIRR Pilot](https://nairrpilot.org/pilotresources)** — the US National AI
  Research Resource: free GPU clusters (H100/A100) **plus** AI-ready datasets
  and pre-trained models from NASA, NIH, DOE and others, for 12-month projects.
  The single best free-at-scale option if you have (or can get) a US research
  affiliation.

## Research corpora (open, ML community)

- **[Hugging Face Datasets](https://huggingface.co/datasets)** — the largest
  community hub; many government/open sets are mirrored here and load in one line.
- **[Kaggle Datasets](https://www.kaggle.com/datasets)** — thousands of curated
  sets, integrated with free Kaggle notebooks.
- **[Papers with Code / OpenML / UCI ML Repository](https://openml.org/)** —
  benchmark datasets tied to published results (ideal reproduction targets).

**Rule of thumb:** if a paper uses a data.gov / NASA / NOAA / NIH / HF / Kaggle
dataset, it is a strong reproduction candidate — the data is a lawful, scriptable
download, so the reproduction hinges only on the method, not on data access.
