# In Silico PGDH

Protein binder design campaign targeting 15-PGDH (PDB: 2GDZ) for the Berlin Bio Hackathon x Adaptyv competition.

**[View designs dashboard](https://alex-hh.github.io/in-silico-pgdh/)**

## Target

- **15-PGDH** (15-hydroxyprostaglandin dehydrogenase, UniProt: P15428)
- 1.65 A crystal structure, homodimer, NAD+ bound
- PDB: [2GDZ](https://www.rcsb.org/structure/2GDZ)

## Strategy

Three binding approaches using BoltzGen on Lyceum:

1. **Active site blocker** — targets the catalytic pocket (Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217)
2. **Dimer disruptor** — targets the homodimer interface (Phe161, Val150, Ala153, Leu167, Tyr206)
3. **Surface (model-free)** — no hotspot constraints, BoltzGen auto-detects binding site

## Structure

```
pgdh_campaign/          # Campaign files, configs, outputs
projects/biolyceum/     # Lyceum platform scripts
resources/biomodals/    # Modal reference scripts
docs/                   # GitHub Pages site
```
