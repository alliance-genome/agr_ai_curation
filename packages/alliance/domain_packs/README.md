# Alliance Domain Packs

Alliance domain packs live in this package-owned directory so LinkML and
curation-database grounding stays outside the provider-agnostic envelope models.

The base scaffold currently defines shared object-role conventions and pinned
Alliance LinkML schema references. Concrete first-pass domain packs, such as
`gene_expression`, keep Alliance-specific LinkML and curation DB grounding in
package metadata and fixtures instead of adding those fields to core schemas.
