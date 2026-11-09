def chunk(items, size):
    """Split items into consecutive lists of `size` elements."""
    return [items[i * size:(i + 1) * size] for i in range(len(items) // size)]
