"""轻量终端交互选择器 — 基于 stdlib termios/tty，无外部依赖。

支持箭头键导航、空格选中、全选/全不选、视口滚动。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field


@dataclass
class PickerItem:
    """选择器中的一个条目。"""

    display: str  # 显示文本（单行）
    selected: bool = False
    meta: object = None  # 附带数据（如 Path）


# ANSI 转义码
_CLEAR = "\033[2K"          # 清除当前行
_MOVE_UP = "\033[1A"        # 光标上移一行
_HIDE_CURSOR = "\033[?25l"  # 隐藏光标
_SHOW_CURSOR = "\033[?25h"  # 显示光标
_RESET = "\033[0m"          # 重置样式
_REVERSE = "\033[7m"        # 反色（高亮）
_GREEN = "\033[32m"         # 绿色
_DIM = "\033[2m"            # 暗淡


def _is_terminal() -> bool:
    """检查 stdout 是否连接到真正的终端。"""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class TerminalPicker:
    """轻量终端交互选择器。

    使用方法::

        items = [
            PickerItem(display="选项 A", meta=data_a),
            PickerItem(display="选项 B", meta=data_b),
        ]
        picker = TerminalPicker(items, title="请选择")
        selected_indices = picker.run()
        if selected_indices is not None:
            for i in selected_indices:
                print(items[i].meta)
    """

    def __init__(
        self,
        items: list[PickerItem],
        *,
        title: str = "",
        max_visible: int = 20,
        allow_multi: bool = True,
    ):
        self.items = items
        self.title = title
        self.max_visible = max_visible
        self.allow_multi = allow_multi

        # 状态
        self._cursor: int = 0
        self._scroll: int = 0  # 视口起始偏移
        self._done: bool = False
        self._cancelled: bool = False

    def run(self) -> list[int] | None:
        """启动交互循环。

        Returns:
            选中项的索引列表，用户取消时返回 None。
        """
        if not self.items:
            return []

        if not _is_terminal():
            # 非 TTY 环境（管道、子进程等）→ 全选
            return list(range(len(self.items)))

        try:
            self._enter_raw_mode()
            self._draw_initial()
            while not self._done:
                key = self._read_key()
                self._handle_key(key)
                if not self._done:
                    self._draw_update()
        except KeyboardInterrupt:
            self._cancelled = True
        finally:
            self._exit_raw_mode()

        if self._cancelled:
            return None

        return [i for i, item in enumerate(self.items) if item.selected]

    # ── 终端模式管理 ──

    def _enter_raw_mode(self) -> None:
        """进入原始终端模式（关闭回显和行缓冲）。"""
        import termios
        import tty

        self._old_settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())

    def _exit_raw_mode(self) -> None:
        """恢复终端模式。"""
        import termios

        if hasattr(self, "_old_settings"):
            termios.tcsetattr(
                sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings
            )
        sys.stdout.write(_SHOW_CURSOR)
        sys.stdout.flush()

    # ── 按键读取 ──

    def _read_key(self) -> str:
        """读取一个按键，处理 ESC 序列（方向键等）。"""
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # ESC 序列
            seq = ch
            # 读取后续字符（方向键: ESC [ A/B/C/D）
            ch2 = sys.stdin.read(1)
            seq += ch2
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                seq += ch3
                if ch3 == "A":
                    return "UP"
                elif ch3 == "B":
                    return "DOWN"
                elif ch3 == "C":
                    return "RIGHT"
                elif ch3 == "D":
                    return "LEFT"
                elif ch3 == "1":  # Home/End 可能是 ESC[1~ 等
                    ch4 = sys.stdin.read(1)
                    return f"ESC[{ch3}{ch4}"
                elif ch3 in ("5", "6"):  # Page Up/Down: ESC[5~ / ESC[6~
                    sys.stdin.read(1)  # 消耗 ~
                    return "PAGE_UP" if ch3 == "5" else "PAGE_DOWN"
                elif ch3 == "H":
                    return "HOME"
                elif ch3 == "F":
                    return "END"
            # 纯 ESC（无后续序列）→ 取消
            if ch2 == "\x1b" or ch2 in ("q",) and len(seq) == 2:
                return "ESC"
            return seq
        elif ch == "\r" or ch == "\n":
            return "ENTER"
        elif ch == " ":
            return "SPACE"
        elif ch == "a":
            return "A"
        elif ch == "q":
            return "Q"
        elif ch == "j":
            return "DOWN"
        elif ch == "k":
            return "UP"
        elif ch == "g":
            return "HOME"
        elif ch == "G":
            return "END"
        elif ch == "\x03":  # Ctrl+C
            raise KeyboardInterrupt
        elif ch == "?":
            return "HELP"
        return ch

    # ── 按键处理 ──

    def _handle_key(self, key: str) -> None:
        n = len(self.items)
        visible = min(self.max_visible, n)

        if key == "UP":
            self._cursor = max(0, self._cursor - 1)
            self._adjust_scroll(visible)
        elif key == "DOWN":
            self._cursor = min(n - 1, self._cursor + 1)
            self._adjust_scroll(visible)
        elif key == "HOME":
            self._cursor = 0
            self._scroll = 0
        elif key == "END":
            self._cursor = n - 1
            self._scroll = max(0, n - visible)
        elif key == "PAGE_UP":
            self._cursor = max(0, self._cursor - visible)
            self._adjust_scroll(visible)
        elif key == "PAGE_DOWN":
            self._cursor = min(n - 1, self._cursor + visible)
            self._adjust_scroll(visible)
        elif key == "SPACE" and self.allow_multi:
            self.items[self._cursor].selected = not self.items[self._cursor].selected
        elif key == "A" and self.allow_multi:
            all_selected = all(it.selected for it in self.items)
            for it in self.items:
                it.selected = not all_selected
        elif key == "Q" and self.allow_multi:
            for it in self.items:
                it.selected = False
        elif key == "ENTER":
            if self.allow_multi:
                selected = [i for i, it in enumerate(self.items) if it.selected]
                if selected:
                    self._done = True
                # 没有选中任何项时，把当前光标项选中并确认
                else:
                    self.items[self._cursor].selected = True
                    self._done = True
            else:
                # 单选模式：直接确认当前项
                self.items[self._cursor].selected = True
                self._done = True
        elif key == "ESC":
            self._cancelled = True
            self._done = True

    def _adjust_scroll(self, visible: int) -> None:
        """确保光标在可见视口内。"""
        if self._cursor < self._scroll:
            self._scroll = self._cursor
        elif self._cursor >= self._scroll + visible:
            self._scroll = self._cursor - visible + 1

    # ── 渲染 ──

    def _draw_initial(self) -> None:
        """首次绘制完整界面。"""
        out = []
        # 隐藏光标
        out.append(_HIDE_CURSOR)

        if self.title:
            out.append(f"\033[1m{self.title}\033[0m")
            out.append("")

        visible = min(self.max_visible, len(self.items))
        for i in range(visible):
            out.append(self._format_line(self._scroll + i, is_cursor=(i == 0)))

        out.append("")
        out.append(self._format_help())
        out.append(self._format_status())

        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()

    def _draw_update(self) -> None:
        """增量更新：重绘可见区域和状态行。"""
        n = len(self.items)
        visible = min(self.max_visible, n)

        # 移动光标到第一个选项行
        lines_to_move = visible + 2  # 选项行 + 空行 + help + 状态
        sys.stdout.write(f"\033[{lines_to_move}A")

        # 重绘所有可见行
        for i in range(visible):
            sys.stdout.write(_CLEAR + "\r")
            sys.stdout.write(self._format_line(self._scroll + i, is_cursor=(self._scroll + i == self._cursor)))
            sys.stdout.write("\n")

        # 空行
        sys.stdout.write(_CLEAR + "\r\n")
        # help 行
        sys.stdout.write(_CLEAR + "\r" + self._format_help() + "\n")
        # 状态行
        sys.stdout.write(_CLEAR + "\r" + self._format_status() + "\n")

        sys.stdout.flush()

    def _format_line(self, idx: int, *, is_cursor: bool) -> str:
        """格式化单行选项。"""
        item = self.items[idx]
        mark = _GREEN + "●" + _RESET if item.selected else _DIM + "○" + _RESET
        num = f"{idx + 1:>4}. "

        # 截断过长显示文本
        display = item.display
        max_display = 80
        if len(display) > max_display:
            display = display[: max_display - 1] + "…"

        line = f"  {mark} {num}{display}"

        if is_cursor:
            return _REVERSE + line + _RESET
        return line

    def _format_help(self) -> str:
        parts = ["↑/↓ 移动"]
        if self.allow_multi:
            parts.append("空格 选中/取消")
            parts.append("a 全选")
            parts.append("q 全不选")
        parts.append("Enter 确认")
        parts.append("Esc 取消")
        return _DIM + "  " + " | ".join(parts) + _RESET

    def _format_status(self) -> str:
        n = len(self.items)
        selected = sum(1 for it in self.items if it.selected)
        if self.allow_multi:
            return f"  已选 {selected}/{n} 个会话，按 Enter 开始提取"
        return f"  {n} 个会话，按 Enter 确认选择"


def pick_items(
    items: list[PickerItem],
    *,
    title: str = "",
    max_visible: int = 20,
) -> list[int] | None:
    """快捷函数：创建选择器并运行。

    Returns:
        选中项索引列表，取消返回 None。
    """
    picker = TerminalPicker(items, title=title, max_visible=max_visible)
    return picker.run()
