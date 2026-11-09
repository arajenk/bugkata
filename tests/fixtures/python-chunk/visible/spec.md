# chunk

`chunk(items, size)` splits the list `items` into consecutive lists of
`size` elements, in order. The last list may be shorter. Every element
appears exactly once. `size` is always at least 1.

```python
chunk([1, 2, 3, 4], 2)  # [[1, 2], [3, 4]]
chunk([], 3)            # []
```
