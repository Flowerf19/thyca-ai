from __future__ import annotations

from thyca.tools.builtin.bash import bash_spec
from thyca.tools.builtin.edit import edit_spec
from thyca.tools.builtin.read import read_spec
from thyca.tools.builtin.write import write_spec
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry


def register_file_tools(registry: ToolRegistry, guard: PathGuard) -> None:
    registry.register(read_spec(guard))
    registry.register(write_spec(guard))
    registry.register(edit_spec(guard))
    registry.register(bash_spec())
