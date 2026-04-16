import main


def test_notes_detail_list_exposes_full_entry_content_without_truncation(frame):
    notebook = frame.notes_store.create_notebook("full content notebook")
    content = "第一段内容比较长，需要完整显示给读屏软件。\n第二段继续补充完整内容。"
    entry = frame.notes_store.create_entry(notebook.id, content, source="manual")

    frame._notes_select_notebook(notebook.id, view="note_detail")

    index = frame._notes_entry_ids.index(entry.id)
    label = frame.notes_entry_list.GetString(index)

    assert "完整显示给读屏软件" in label
    assert "第二段继续补充完整内容" in label
