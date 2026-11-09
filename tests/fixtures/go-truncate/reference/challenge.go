//go:build !fixed

package truncate

// Truncate returns s cut to at most n characters.
func Truncate(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}
