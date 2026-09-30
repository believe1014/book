"""MCP 章節內容 Markdown 雙向轉換(get_chapter_content / update_chapter_content)。

doc 形狀以前端 RichTextEditor.htmlToDoc 實際產出為準。
"""
import json

import pytest

from app.mcp_server import _doc_to_md, _md_to_doc


def T(text, *marks):
    n = {"type": "text", "text": text}
    if marks:
        n["marks"] = [{"type": m} for m in marks]
    return n


def P(*kids):
    return {"type": "paragraph", **({"content": list(kids)} if kids else {})}


def LI(*kids):
    return {"type": "listItem", "content": list(kids)}


IMG = {"type": "image", "attrs": {"src": "/storage/1/a.png", "alt": "圖一"}}

EDITOR_DOCS = {
    "paragraphs+empty": [P(T("第一段")), P(), P(T("第三段"))],
    "headings": [{"type": "heading", "attrs": {"level": lv}, "content": [T(f"H{lv}")]} for lv in (1, 2, 3)],
    "marks": [P(T("a "), T("粗", "bold"), T("斜", "italic"), T("底", "underline"),
                T("粗斜", "bold", "italic"), T("全", "bold", "italic", "underline"), T(" z"))],
    "adjacent-marks": [P(T("a", "italic"), T("b", "bold"), T("c", "bold", "italic"), T("d", "bold"))],
    "blockquote": [{"type": "blockquote", "content": [T("引用 "), T("重點", "bold")]}],
    "bullet+nested": [{"type": "bulletList", "content": [
        LI(T("甲")),
        LI(T("乙"), {"type": "orderedList", "content": [LI(T("乙1")), LI(T("乙2", "italic"))]}),
        LI(T("丙")),
    ]}],
    "ordered": [{"type": "orderedList", "content": [LI(T("一")), LI(T("二"))]}],
    "list-then-list": [{"type": "bulletList", "content": [LI(T("x"))]},
                       {"type": "orderedList", "content": [LI(T("y"))]}],
    "code": [{"type": "codeBlock", "attrs": {"language": "mermaid"},
              "content": [T("graph TD\n  A-->B\n")]},
             {"type": "codeBlock", "attrs": {"language": ""}}],
    "image-in-paragraph": [P(T("見 "), IMG, T(" 說明")), P(IMG)],
    "literal-markup": [P(T("5 * 3 = 15, **不是粗體**, <u>x</u>, ![a](b), C:\\*path\\")),
                       P(T("# 不是標題")), P(T("- 不是清單")), P(T("12. 不是編號")),
                       P(T("> 不是引用")), P(T("```mermaid")), P(T("```"))],
}


@pytest.mark.parametrize("name", EDITOR_DOCS)
def test_round_trip_doc_md_doc(name):
    doc = {"type": "doc", "content": EDITOR_DOCS[name]}
    md = _doc_to_md(json.dumps(doc))
    assert _md_to_doc(md) == doc, md


def test_markdown_output_is_formatted():
    doc = {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 2}, "content": [T("標題")]},
        P(T("粗", "bold"), T("斜", "italic")),
        {"type": "bulletList", "content": [LI(T("a"), {"type": "bulletList", "content": [LI(T("b"))]})]},
        {"type": "blockquote", "content": [T("q")]},
    ]}
    assert _doc_to_md(json.dumps(doc)) == "## 標題\n**粗***斜*\n- a\n  - b\n> q"


def test_plain_text_input_still_works():
    doc = _md_to_doc("第一行\n\n第二行\n```mermaid\ngraph TD\n```")
    assert doc["content"] == [
        P(T("第一行")), P(), P(T("第二行")),
        {"type": "codeBlock", "attrs": {"language": "mermaid"}, "content": [T("graph TD")]},
    ]
    assert _md_to_doc("")["content"] == [P()]


def test_unsupported_markdown_keeps_all_text():
    src = ("#### 四級標題\n[連結](https://x.y)\n`code`\n---\n| a | b |\n"
           "未閉合 **粗\n```py\n沒有結尾的 fence")
    doc = _md_to_doc(src)
    assert all(n["type"] == "paragraph" for n in doc["content"])
    got = ["".join(t["text"] for t in n.get("content", [])) for n in doc["content"]]
    assert got == src.split("\n")


def test_bad_or_empty_json_reads_as_empty():
    assert _doc_to_md("") == ""
    assert _doc_to_md("not json") == ""
