def chunk(items, size):
    """Split items into consecutive lists of `size` elements."""
    return [items[i:i + size] for i in range(0, len(items), size)]
