# -*- coding: utf-8 -*-
"""真机 UI 端到端验证：本次修改的 5 个功能点。

用真实 wx.App + ChatFrame（隐藏窗口）走完整 UI 路径：
1. 笔记条目菜单：置顶/取消置顶/置底/取消置底
2. codex 执行过程列表不串线（历史聊天 A 不显示活动聊天 B 的内容）
3. Alt+A 清除上下文后自动重发第一条消息
4. 清除上下文后向手机端推送 history_changed + state（空 turns）
5. 回答列表微信式时间行（独立行 = 独立键盘焦点）

运行：.venv/Scripts/python scripts/e2e_features_validation.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["AUTO_START_QUICK_TUNNEL"] = "0"
os.environ["REMOTE_CONTROL_AUTOSTART"] = "0"
os.environ["DESKTOP_FILE_SERVICE_AUTOSTART"] = "0"
for _name in (
    "REMOTE_CONTROL_TOKEN", "REMOTE_CONTROL_HOST", "REMOTE_CONTROL_PORT", "REMOTE_CONTROL_DOMAIN",
    "CLAUDECODE_REMOTE_CONTROL_TOKEN", "CLAUDECODE_REMOTE_CONTROL_HOST",
    "CLAUDECODE_REMOTE_CONTROL_PORT", "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
):
    os.environ.pop(_name, None)

import wx  # noqa: E402
import main  # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="e2e_features_"))
main.resolve_app_data_dir = lambda: _tmp
main.resolve_notes_data_dir = lambda: _tmp / "notes"
main.GlobalCtrlTapHook.start = lambda self: None
main.GlobalCtrlTapHook.stop = lambda self: None
main.ChatFrame._legacy_state_paths = lambda self: [self.state_path]

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


app = wx.App(False)
frame = main.ChatFrame()
frame.Show()
frame._save_state = lambda *a, **k: None
frame._defer_chat_state_save = lambda: None
frame._mark_openclaw_lifecycle_dirty = lambda: None
_pushed = []
frame._push_remote_history_changed = lambda *a, **k: _pushed.append(("history_changed",) + tuple(a))
frame._push_remote_state = lambda *a, **k: _pushed.append(("state",) + tuple(a))


def capture_menu():
    captured = {"items": []}
    frame.PopupMenu = lambda menu: captured.__setitem__(
        "items",
        [(it.GetItemLabelText(), it.GetId(), it.IsEnabled())
         for it in menu.GetMenuItems() if not it.IsSeparator()],
    )
    return captured


def fire_menu(item_id):
    evt = wx.CommandEvent(wx.wxEVT_MENU, item_id)
    evt.SetEventObject(frame)
    frame.ProcessEvent(evt)


# ---------- 功能 1：笔记条目菜单 置顶/取消置顶/置底/取消置底 ----------
nb = frame.notes_store.create_notebook("e2e notebook")
e1 = frame.notes_store.create_entry(nb.id, "entry one", source="manual", sort_order=10)
e2 = frame.notes_store.create_entry(nb.id, "entry two", source="manual", sort_order=20)
e3 = frame.notes_store.create_entry(nb.id, "entry three", source="manual", sort_order=30)
frame._notes_select_notebook(nb.id, view="note_detail")
frame.notes_notebook_list.HasFocus = lambda: False
frame.notes_entry_list.HasFocus = lambda: True

# 置顶：选中 e2，菜单应含「置顶笔记条目」，触发后 pinned=True 且排到最前
frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(e2.id))
cap = capture_menu()
frame._show_notes_menu()
labels = [lb for lb, _i, _en in cap["items"]]
check("F1 菜单含置顶/置底项", "置顶笔记条目" in labels and "置底笔记条目" in labels, str(labels))
fire_menu(next(i for lb, i, _en in cap["items"] if lb == "置顶笔记条目"))
check("F1 置顶后 pinned=True", frame.notes_store.get_entry(e2.id).pinned is True)
check("F1 置顶后排最前", frame.notes_store.list_entries(nb.id)[0].id == e2.id)

# 取消置顶：动态标签变为「取消置顶笔记条目」
frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(e2.id))
cap = capture_menu()
frame._show_notes_menu()
labels = [lb for lb, _i, _en in cap["items"]]
check("F1 已置顶条目显示取消置顶", "取消置顶笔记条目" in labels and "置顶笔记条目" not in labels)
fire_menu(next(i for lb, i, _en in cap["items"] if lb == "取消置顶笔记条目"))
check("F1 取消置顶后 pinned=False", frame.notes_store.get_entry(e2.id).pinned is False)
check("F1 取消置顶后选中保持", frame._notes_selected_entry_id() == e2.id)

# 置底：e1 置底后排最后
frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(e1.id))
cap = capture_menu()
frame._show_notes_menu()
fire_menu(next(i for lb, i, _en in cap["items"] if lb == "置底笔记条目"))
check("F1 置底后排最后", frame.notes_store.list_entries(nb.id)[-1].id == e1.id)

# 取消置底：仅最后一条启用；触发后移回非置顶区最上方
frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(e1.id))
cap = capture_menu()
frame._show_notes_menu()
unbottom = next((lb, i, en) for lb, i, en in cap["items"] if lb == "取消置底笔记条目")
check("F1 底部条目的取消置底可用", unbottom[2] is True)
fire_menu(unbottom[1])
check("F1 取消置底后回非置顶区最上方", frame.notes_store.list_entries(nb.id)[0].id == e1.id)
check("F1 取消置底后选中保持", frame._notes_selected_entry_id() == e1.id)
# 非底部条目取消置底禁用
frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(e1.id))
cap = capture_menu()
frame._show_notes_menu()
en = next(en for lb, _i, en in cap["items"] if lb == "取消置底笔记条目")
# e1 现在恰好又是第一条（非底部），应禁用
check("F1 非底部条目取消置底禁用", en is False)

# ---------- 功能 2：codex 执行过程列表不串线 ----------
frame.archived_chats = [
    {
        "id": "chat-a",
        "title": "chat a",
        "model": main.DEFAULT_CODEX_MODEL,
        "created_at": 1.0,
        "updated_at": 1.0,
        "detail_panel_mode": "execution",
        "turns": [{"question": "A 的提问", "answer_md": "A 的回答",
                   "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}],
        "execution_steps": [{"display_kind": "commentary", "list_text": "A 的执行步骤",
                             "detail_text": "A 的执行步骤", "turn_idx": 0}],
    }
]
frame._on_new_chat_clicked(None)
frame.active_session_turns.append(
    {"question": "B 的提问", "answer_md": "B 的回答", "model": main.DEFAULT_CODEX_MODEL, "created_at": 2.0}
)
frame.active_turn_idx = 0
check("F2 打开历史聊天 A", frame._show_history_chat("chat-a") is True)
frame._apply_detail_panel_mode("execution", refresh_execution=True)
hist_rows = list(frame.execution_list.GetStrings())
check("F2 历史 A 的执行列表含 A 的内容", any("A 的执行步骤" in r for r in hist_rows))
check("F2 历史 A 的执行列表不含 B 的提问/回答",
      not any("B 的提问" in r or "B 的回答" in r for r in hist_rows), str(hist_rows))
frame._show_history_chat(frame.active_chat_id)
frame._apply_detail_panel_mode("execution", refresh_execution=True)
act_rows = list(frame.execution_list.GetStrings())
check("F2 活动模式仍正常显示 B 的上下文", any("B 的提问" in r for r in act_rows))

# ---------- 功能 3 + 4：Alt+A 清空后重发首条 + 推送 state ----------
frame._on_new_chat_clicked(None)
frame.active_session_turns = [
    {"question": "你好", "answer_md": "你好！有什么可以帮你？", "model": main.DEFAULT_MODEL_ID,
     "created_at": time.time() - 100},
    {"question": "第二问", "answer_md": "第二答", "model": main.DEFAULT_MODEL_ID,
     "created_at": time.time() - 50},
]
_sent = []
frame._submit_question = lambda q, source="local", **k: _sent.append((q, source))
_pushed.clear()
ok = frame._clear_context_and_start_new_chat(auto_resend_first=True)
check("F3 Alt+A 清空返回成功", ok is True)
check("F3 自动重发首条消息", _sent == [("你好", "local")], str(_sent))
check("F4 推送 history_changed", any(p[0] == "history_changed" for p in _pushed))
check("F4 推送 state（手机端同步清空）", any(p[0] == "state" for p in _pushed), str(_pushed))

# 默认参数（远程路径）不重发
frame.active_session_turns = [
    {"question": "你好", "answer_md": "答", "model": main.DEFAULT_MODEL_ID, "created_at": time.time()}
]
_sent.clear()
frame._clear_context_and_start_new_chat()
check("F3 远程/默认路径不重发", _sent == [])

# ---------- 功能 5：回答列表微信式时间行 ----------
frame._on_new_chat_clicked(None)
now = time.time()
t_old = now - 9 * 86400          # 更早：yyyy年M月d日 HH:mm
t_week = now - 2 * 86400         # 一周内：星期X HH:mm
t_today = now - 600              # 当天：HH:mm（与上周期间隔>5分钟，显示）
t_close = now - 400              # 与上一条间隔 200 秒 < 300，不显示
frame.active_session_turns = [
    {"question": "q1", "answer_md": "a1", "model": main.DEFAULT_MODEL_ID, "created_at": t_old},
    {"question": "q2", "answer_md": "a2", "model": main.DEFAULT_MODEL_ID, "created_at": t_week},
    {"question": "q3", "answer_md": "a3", "model": main.DEFAULT_MODEL_ID, "created_at": t_today},
    {"question": "q4", "answer_md": "a4", "model": main.DEFAULT_MODEL_ID, "created_at": t_close},
]
frame._render_answer_list()
rows = list(frame.answer_list.GetStrings())
lb_old = main.wechat_time_label(t_old, now)
lb_week = main.wechat_time_label(t_week, now)
lb_today = main.wechat_time_label(t_today, now)
lb_close = main.wechat_time_label(t_close, now)
check("F5 首条消息显示时间行", lb_old in rows, f"expect {lb_old!r} in {rows!r}")
check("F5 一周内显示星期格式", lb_week in rows and lb_week.startswith("星期"), lb_week)
check("F5 当天显示 HH:mm", lb_today in rows, lb_today)
check("F5 间隔<5分钟不重复显示", lb_close not in rows or lb_close == lb_today,
      f"lb_close={lb_close!r} rows={rows!r}")
check("F5 时间行是独立行（独立键盘焦点）",
      all(isinstance(r, str) for r in rows) and rows.count(lb_old) == 1 and lb_old in rows
      and rows.index(lb_old) < next(i for i, r in enumerate(rows) if "q1" in r))

print()
_failed = [n for n, ok_, _d in _results if not ok_]
print(f"总计 {len(_results)} 项，通过 {len(_results) - len(_failed)}，失败 {len(_failed)}")
if _failed:
    print("失败项：")
    for n in _failed:
        print(f"  - {n}")

frame.Destroy()
try:
    app.Yield()
except Exception:
    pass
sys.exit(1 if _failed else 0)
