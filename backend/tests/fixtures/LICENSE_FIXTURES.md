# Test Fixture Licenses

## alliance/gene_product_resolution/rno_mir_124_3p.json

- **Sources**: Read-only Alliance curation database identity rows, RNAcentral search/API cross-references, and miRBase mature/hairpin records for rat miR-124-3p
- **Source URLs**: https://www.ebi.ac.uk/ebisearch/ws/rest/rnacentral, https://rnacentral.org/api/v1/rna/URS000020BE6A/xrefs/10116/, https://www.mirbase.org/mature/MIMAT0000828, and https://mirbase.org/download/CURRENT/hairpin.fa
- **Licenses**: RNAcentral CC0; miRBase public domain; Alliance/RGD rows retained as minimal identifier evidence from the repository's configured read-only curation source
- **License URLs**: https://rnacentral.org/downloads and https://mirbase.org/download/CURRENT/LICENSE/
- **Retrieved**: 2026-08-27
- **Usage**: Typed mature-product, precursor-locus, one-to-many candidate, provenance, and no-arbitrary-selection resolver tests

## alliance/go_annotations/rgd_620474_go_api.json and quickgo_rgd_620474_rejection.json

- **Source**: Gene Ontology Consortium API response and QuickGO identifier-validation response for `RGD:620474`
- **Source URLs**: https://api.geneontology.org/api/bioentity/gene/RGD:620474/function?rows=-1 and https://www.ebi.ac.uk/QuickGO/services/annotation/search?geneProductId=RGD:620474
- **License**: Creative Commons Attribution 4.0 International (CC BY 4.0)
- **License URL**: https://creativecommons.org/licenses/by/4.0/
- **Retrieved**: 2026-08-26
- **Usage**: Typed existing-GO annotation adapter contract testing with RGD reference, With/From, product type, provider, and source-record provenance

## micropub-biology-001725.pdf

- **Title**: Analysis of Transcripts in the Fly Cell Atlas Reveals Additional Cell Populations in the Drosophila melanogaster Ovary
- **Authors**: Mendoza Andrade O, Wright Z, Ghasemzadeh S, Bergstralh DT
- **DOI**: 10.17912/micropub.biology.001725
- **Publisher**: microPublication Biology
- **License**: Creative Commons Attribution 4.0 International (CC BY 4.0)
- **License URL**: https://creativecommons.org/licenses/by/4.0/
- **Pages**: 6
- **Retrieved**: 2026-02-12
- **Usage**: End-to-end pipeline integration testing (PDFX parsing, chunking, Weaviate storage)

## sample_fly_publication.pdf

- **Title**: Unknown (pre-existing fixture)
- **License**: Creative Commons Attribution 4.0 International (CC BY 4.0)
- **License URL**: https://creativecommons.org/licenses/by/4.0/
- **Verified**: 2026-02-12
- **Note**: License verified by project maintainer.
