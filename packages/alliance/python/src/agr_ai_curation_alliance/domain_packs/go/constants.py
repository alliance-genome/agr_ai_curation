"""Constants for the Alliance GO curator-review builder."""

GO_DOMAIN_PACK_ID = "agr.alliance.go"
GO_DOMAIN_PACK_VERSION = "0.2.0"
GO_MATERIALIZER_ID = "agr.alliance.go.builder_materializer"
GO_MODEL_ID = "GOCuratableObjectPayload"
GO_OBJECT_TYPE = "GOCuratableObject"
GO_OBJECT_ROLE = "curatable_unit"
# The builder's own deterministic lookup for a proposed GO experimental
# evidence code: each code resolves to its ECO class, and any other code stays
# unresolved.
GO_EVIDENCE_CODE_ECO = {
    "EXP": "ECO:0000269",
    "IDA": "ECO:0000314",
    "IPI": "ECO:0000353",
    "IMP": "ECO:0000315",
    "IGI": "ECO:0000316",
    "IEP": "ECO:0000270",
}

__all__ = [
    "GO_DOMAIN_PACK_ID",
    "GO_EVIDENCE_CODE_ECO",
    "GO_DOMAIN_PACK_VERSION",
    "GO_MATERIALIZER_ID",
    "GO_MODEL_ID",
    "GO_OBJECT_ROLE",
    "GO_OBJECT_TYPE",
]
