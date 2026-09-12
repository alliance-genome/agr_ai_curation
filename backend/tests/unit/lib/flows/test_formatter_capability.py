from types import SimpleNamespace
import pytest
from src.lib.flows.formatter_capability import snapshot_formatter_format, resolved_formatter_format

@pytest.mark.parametrize("template,tools,output,expected", [
    ("tsv_formatter", ["finalize_and_save"], "none", "tsv"),
    ("csv_formatter", ["finalize_and_save"], "none", "csv"),
    ("json_formatter", ["finalize_and_save"], "none", "json"),
    ("tsv_formatter", [], "none", None),
    ("pdf_extraction", ["finalize_and_save"], "none", None),
    ("tsv_formatter", ["finalize_and_save"], "structured_extraction", None),
])
def test_saved_formatter_requires_template_contract_and_tool(template, tools, output, expected):
    saved = SimpleNamespace(template_source=template, tool_ids=tools, output_contract=SimpleNamespace(output_state=output))
    assert snapshot_formatter_format(saved) == expected


def test_curator_labels_do_not_define_executable_format():
    assert resolved_formatter_format('ca_test', {'category': 'Output', 'name': 'CSV formatter'}) is None
    assert resolved_formatter_format('ca_test', {'output_formatter_format': 'tsv'}) == 'tsv'
