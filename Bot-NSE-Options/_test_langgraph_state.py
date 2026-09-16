import sys
sys.path.insert(0, '.')
from typing import TypedDict, Optional, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

class MyState(TypedDict):
    x: Optional[int]
    y: Optional[int]

def node_a(s):
    return {'x': 1, 'y': 2}

def node_b(s):
    print(f"node_b sees x={s.get('x')} y={s.get('y')}")
    return {'y': 99}          # deliberately omit 'x'

def node_c(s):
    print(f"node_c sees x={s.get('x')} y={s.get('y')}")
    return {}

b = StateGraph(MyState)
b.add_node('a', node_a)
b.add_node('b', node_b)
b.add_node('c', node_c)
b.set_entry_point('a')
b.add_edge('a', 'b')
b.add_edge('b', 'c')
b.add_edge('c', END)
g = b.compile(checkpointer=MemorySaver())
r = g.invoke({'x': None, 'y': None}, config={'configurable': {'thread_id': 't2'}})
print(f"final x={r.get('x')} y={r.get('y')}")
assert r.get('x') == 1, "TypedDict should preserve x across nodes"
assert r.get('y') == 99, "y should be 99 from node_b"
print("PASS — TypedDict preserves state across nodes")
