"""A flow with two disease steps and one gene expression step fits the response cap."""

from uuid import uuid4

from fastapi.responses import JSONResponse

from src.lib.benchmarks.flow_contracts import BenchmarkFlowOutputContract, PackStructureSource
from src.lib.benchmarks.pack_catalog import benchmark_pack_catalog
from src.lib.benchmarks.saved_flows import (
    BenchmarkSavedFlowContracts,
    BenchmarkSavedFlowNodeContract,
    BenchmarkSavedFlowSummary,
)
from src.lib.benchmarks.suites import _digest
from src.lib.flows.validation_attachments import domain_pack_validation_registries
from src.lib.openai_agents.config import get_benchmark_catalog_max_response_bytes


def node(node_id, pack_id):
    catalog = benchmark_pack_catalog(domain_pack_validation_registries()[pack_id].domain_pack)
    return BenchmarkSavedFlowNodeContract(
        node_id=node_id, title=node_id, output_key=node_id, output_kind="pack_fields",
        contract=BenchmarkFlowOutputContract(
            status="verified", representation="pack_fields", schema_definition=catalog,
            schema_digest=_digest(catalog), domain_pack_id=pack_id,
            structure_source=PackStructureSource(pack_id=pack_id,
                                                 pack_version=catalog["pack_version"],
                                                 pack_label=catalog["pack_label"])))


def test_three_pack_steps_fit_the_catalog_response_cap():
    result = BenchmarkSavedFlowContracts(
        flow=BenchmarkSavedFlowSummary(flow_id=uuid4(), title="Big", description=None,
                                       revision="sha256:" + "a" * 64),
        nodes=(node("a", "agr.alliance.disease"), node("b", "agr.alliance.disease"),
               node("c", "agr.alliance.gene_expression")),
        status="verified", runnable=False, run_problem="x")
    body = JSONResponse(result.model_dump(mode="json")).body
    assert len(body) < get_benchmark_catalog_max_response_bytes()
