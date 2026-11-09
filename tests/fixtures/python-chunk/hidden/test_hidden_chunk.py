import pytest

from target import chunk


def test_hidden_chunk_uneven():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


@pytest.mark.parametrize("n", [1, 3, 7])
def test_hidden_chunk_keeps_every_item(n):
    items = list(range(10))
    assert sum(chunk(items, n), []) == items
