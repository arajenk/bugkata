package truncate

import "testing"

func TestHiddenTruncateMultibyte(t *testing.T) {
	if got := Truncate("héllo", 2); got != "hé" {
		t.Fatalf("Truncate(héllo, 2) = %q, want %q", got, "hé")
	}
}
