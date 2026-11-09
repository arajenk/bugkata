//go:build !fixed

package truncate

// Truncate returns s cut to at most n characters.
func Truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
