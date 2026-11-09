package truncate

import "testing"

func TestTruncateASCII(t *testing.T) {
	if got := Truncate("hello", 3); got != "hel" {
		t.Fatalf("Truncate(hello, 3) = %q, want %q", got, "hel")
	}
	if got := Truncate("hi", 5); got != "hi" {
		t.Fatalf("Truncate(hi, 5) = %q, want %q", got, "hi")
	}
}
