"""Offset paging with a (chunkIndex, id) sort against the real test Weaviate.

Section reads, the outline build and the chunk API page a tenant collection with
a documentId filter, an offset and ``Sort.by_property("chunkIndex").by_id()``.
The unit tests check that the sort is requested; this checks that the server
accepts sorting by ``_id`` together with a filter, an offset and a tenant, and
that every object comes back exactly once in (chunkIndex, id) order, including
two objects that share a chunkIndex across a page boundary.
"""

from __future__ import annotations

from weaviate.classes.data import DataObject
from weaviate.classes.query import Filter

from src.lib.weaviate_client import chunks

DOCUMENT_ID = "doc-total-order"
OTHER_DOCUMENT_ID = "doc-other"


def _uuid(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012d}"


def test_every_object_is_read_once_in_chunk_index_then_id_order(chunk_collection, clean_chunks):
    page = chunks.get_weaviate_chunk_fetch_page_size()
    tied_index = page - 1
    objects = [(index, _uuid(1000 + index)) for index in range(page + 10) if index != tied_index]
    # Two objects share the chunkIndex that straddles the first page boundary.
    # Their ids are inserted in reverse order, so only the id sort orders them.
    objects += [(tied_index, _uuid(9002)), (tied_index, _uuid(9001))]
    other = [(index, _uuid(5000 + index)) for index in range(5)]

    result = chunk_collection.data.insert_many([
        DataObject(uuid=uuid, properties={"documentId": document_id, "chunkIndex": index,
                                          "content": f"{document_id} passage {index}"})
        for document_id, rows in ((DOCUMENT_ID, objects), (OTHER_DOCUMENT_ID, other))
        for index, uuid in rows
    ])
    assert not result.errors

    read = chunks._fetch_every_object(
        chunk_collection,
        filters=Filter.by_property("documentId").equal(DOCUMENT_ID),
        return_properties=["chunkIndex", "documentId"],
    )

    got = [(obj.properties["chunkIndex"], str(obj.uuid)) for obj in read]
    assert len(got) > page
    assert got == sorted(objects)
    assert {obj.properties["documentId"] for obj in read} == {DOCUMENT_ID}
