package main

import "testing"

func TestInvalidRootHasMacro(t *testing.T) {
	if err := validate([]check{{Name: "bad root guard", Expression: "has(status)"}}); err == nil {
		t.Fatal("invalid top-level has macro was accepted")
	}
}

func TestPresenceAndCompletion(t *testing.T) {
	checks := []check{{Name: "completion", Expression: "has(status.phase) && status.phase == 'completed'",
		Cases: []fixture{
			{Object: map[string]any{}, Want: false, AllowError: true},
			{Object: map[string]any{"status": map[string]any{}}, Want: false},
			{Object: map[string]any{"status": map[string]any{"phase": "started"}}, Want: false},
			{Object: map[string]any{"status": map[string]any{"phase": "failed"}}, Want: false},
			{Object: map[string]any{"status": map[string]any{"phase": "completed"}}, Want: true},
		}}}
	if err := validate(checks); err != nil {
		t.Fatal(err)
	}
}
