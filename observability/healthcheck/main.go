// Compile Flux health expressions using the live controller's CEL-Go version.
package main

import (
	"encoding/json"
	"fmt"
	"os"

	"cel.dev/cel-go/cel"
	"cel.dev/cel-go/common/types"
)

type fixture struct {
	Object     map[string]any
	Want       bool
	AllowError bool
}

type check struct {
	Name       string
	Expression string
	Cases      []fixture
}

func environment() (*cel.Env, error) {
	return cel.NewEnv(cel.Variable("status", cel.DynType), cel.Variable("spec", cel.DynType),
		cel.Variable("metadata", cel.DynType), cel.Variable("apiVersion", cel.StringType),
		cel.Variable("kind", cel.StringType))
}

func validate(checks []check) error {
	env, err := environment()
	if err != nil {
		return err
	}
	for _, item := range checks {
		ast, issues := env.Compile(item.Expression)
		if issues.Err() != nil {
			return fmt.Errorf("%s: %w", item.Name, issues.Err())
		}
		if ast.OutputType() != cel.BoolType {
			return fmt.Errorf("%s must return a bool", item.Name)
		}
		program, err := env.Program(ast)
		if err != nil {
			return err
		}
		for _, example := range item.Cases {
			result, _, err := program.Eval(example.Object)
			if err != nil && example.AllowError && !example.Want {
				continue
			}
			if err != nil {
				return fmt.Errorf("%s: fixture evaluation failed: %w", item.Name, err)
			}
			if (result == types.True) != example.Want {
				return fmt.Errorf("%s: unexpected fixture result", item.Name)
			}
		}
	}
	return nil
}

func main() {
	var checks []check
	if err := json.NewDecoder(os.Stdin).Decode(&checks); err != nil {
		panic(err)
	}
	if err := validate(checks); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("PASS: compiled %d Flux health expressions and evaluated backup lifecycle fixtures\n", len(checks))
}
