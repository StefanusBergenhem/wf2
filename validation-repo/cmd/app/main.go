// Command app prints a greeting (a one-edge dependency on greeter).
package main

import (
	"fmt"

	"validationrepo/greeter"
)

func main() {
	fmt.Println(greeter.Greet("world"))
}
