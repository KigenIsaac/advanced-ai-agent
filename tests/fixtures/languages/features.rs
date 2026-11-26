#![allow(unused)]
extern crate core;
use std::{fmt, sync::Arc};
pub mod inner {}
pub struct Point { x: usize }
enum Message { Ready, Value(usize) }
union Word { i: i32, u: u32 }
trait Map<T>: Send { type Out; fn map(&self, x: T) -> Self::Out; }
impl Map<u32> for Point { type Out = u32; fn map(&self, x:u32) -> u32 { x } }
extern "C" { fn work(); }
type Callback = fn(usize) -> usize;
const SIZE: usize = 2;
static VALUES: [usize; 2] = [1, 2];
macro_rules! twice { ($x:expr) => { $x + $x }; }
#[inline]
fn walk<'a, T>(v: &'a [T], p: *const i32, d: &dyn Send) -> impl Iterator<Item = usize>
where T: Copy {
    let pair: (u8, u8) = (1, 2);
    let (a, b) = pair;
    let borrowed = &pair;
    let f = |x| x + 1;
    let task = async { fut.await };
    let n = unsafe { *p } as usize;
    let point = Point { x: n };
    if let Some(x) = option { log(x); }
    for i in 0..2 { while running() { break; } }
    loop { break; }
    match v { [x, ..] => (), _ => () }
    match a { 1 | 2 => (), _ => () }
    twice!(a);
    (0..2).map(f)
}
fn fallible() -> Result<(), E> { work_result()?; Ok(()) }
