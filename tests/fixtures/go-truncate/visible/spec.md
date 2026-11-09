# truncate

`Truncate(s string, n int) string` returns `s` cut to at most `n` characters.
A character is a Unicode code point (rune). If `s` has `n` or fewer
characters it is returned unchanged. Nothing is appended. `n` is never
negative.

```go
Truncate("hello", 3) // "hel"
Truncate("hi", 5)    // "hi"
```
