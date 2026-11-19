"""ALL-1561: FLOW_FINISHED says how many records the review set aside and whether each output holds them."""

from src.lib.flows.executor import _build_set_aside_summary


def _step(result_id, set_aside):
    return {"step": 1, "node_id": f"node-{result_id}", "extraction_result_id": result_id,
            "validation_review": {"episode_id": f"episode-{result_id}", "ready": not set_aside,
                                  "set_aside_record_target_ids": set_aside}}


def test_no_set_aside_record_gives_no_summary():
    steps = [_step("one", []), {"step": 2, "node_id": "formatter"}]
    assert _build_set_aside_summary(steps, []) is None


def test_the_summary_counts_records_and_reads_each_outputs_own_counts():
    steps = [_step("one", ["record-a", "record-b"]), _step("two", []), _step("three", ["record-c"])]
    outputs = [
        {"type": "FILE_READY", "details": {"file_id": "file-1", "filename": "genes.tsv",
                                           "formatter_node_id": "tsv", "set_aside": {"included": 0, "left_out": 3}}},
        {"type": "FILE_READY", "details": {"file_id": "file-2", "filename": "genes.json",
                                           "formatter_node_id": "json", "set_aside": {"included": 3, "left_out": 0}}},
        {"type": "CHAT_OUTPUT_READY", "details": {"output": "table", "formatter_node_id": "chat", "set_aside": None}},
    ]
    summary = _build_set_aside_summary(steps, outputs)
    assert summary == {
        "count": 3,
        "results": [
            {"extraction_result_id": "one", "node_id": "node-one", "episode_id": "episode-one", "count": 2},
            {"extraction_result_id": "three", "node_id": "node-three", "episode_id": "episode-three", "count": 1},
        ],
        "outputs": [
            {"type": "FILE_READY", "formatter_node_id": "tsv", "file_id": "file-1", "filename": "genes.tsv",
             "included": 0, "left_out": 3},
            {"type": "FILE_READY", "formatter_node_id": "json", "file_id": "file-2", "filename": "genes.json",
             "included": 3, "left_out": 0},
        ],
        "left_out_of_outputs": 3,
        "included_in_outputs": 3,
    }
