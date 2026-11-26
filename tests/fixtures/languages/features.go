package demo
import "fmt"
type Alias = int
type Number interface { ~int | ~int64 }
type Reader interface { Read([]byte) (int, error) }
type Box[T Number] struct { Value T }
const N = 2
var items []int
func (b *Box[T]) Get() T { return b.Value }
func worker[T Number](arg T, rest ...int) {
    var p *int
    var a [2]int
    var f func(int) int
    var q chan int
    var v any
    list := [...]int{2, 3}
    _ = Box[int]{Value: 1}
    m := map[string]int{"size": 2}
    g := func() { fmt.Print(a, m) }
    go g()
    defer g()
    q <- 4
    select { case x := <-q: fmt.Print(x); default: }
    switch z := v.(type) { case int: fmt.Print(z) }
    switch arg { case 1: fallthrough; default: }
    _ = v.(int)
    for i := range list { if i > 0 { fmt.Print(list[0:i], list[i]) } }
}
