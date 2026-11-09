from target import chunk


def test_chunk_even_split():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_chunk_empty():
    assert chunk([], 3) == []
