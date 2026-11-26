using System;
namespace Demo {
[Obsolete] interface I<T> where T : class { T Get(); }
public delegate int Mapper(int x);
public enum Kind { Ready }
public record Row(int Id);
public struct Pair { public int A; }
public class Box : IDisposable {
    public event EventHandler Changed;
    int field;
    public int Value { get; set; }
    public int this[int i] => i;
    public Box() {}
    ~Box() {}
    public static Box operator +(Box a, Box b) => a;
    public static implicit operator int(Box a) => 0;
    public void Dispose() {}
    public unsafe void Raw(int* p, delegate*<void> f, byte[] bytes) {
        unsafe { int* tmp = stackalloc int[3]; }
        fixed (byte* b = bytes) { }
        checked { field++; }
    }
    public async Task Work(string? s, int[] xs) {
        int Local() => 1;
        (int x,int y) pair = (1,2);
        Func<int,int> inc = x => x + 1;
        Action a = delegate { };
        await Run();
        using (var obj = new Box()) { lock (obj) { } }
        try { throw new Exception(); }
        catch (Exception e) when (e != null) {}
        finally {}
        int n = field switch { 1 => 2, _ => 0 };
        bool ok = xs is [1, ..] && this is Box { Value: 1 };
        bool matches = xs is [1,2];
        var v = s?.Length;
        int[] numbers = [1,2];
        var list = new List<int> {1,2};
        var row = new Row(1) with { Id = 2 };
        var query = from x in xs join y in xs on x equals y group x by y;
        var msg = $"Count {n}";
        var slice = xs[1..];
        foreach (var x in xs) {}
    }
    public IEnumerable<int> Values() { yield return field; }
}
}
