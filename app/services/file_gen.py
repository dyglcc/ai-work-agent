"""文件生成服务：PPT、Word、图表、图片"""
from __future__ import annotations

import io
import logging
from typing import Any

import httpx
from pptx import Presentation
from pptx.util import Inches, Pt
from docx import Document
from docx.shared import Pt as DocxPt, RGBColor
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
import matplotlib
matplotlib.use('Agg')  # 非 GUI 后端
import matplotlib.pyplot as plt

from app.config import settings

logger = logging.getLogger(__name__)


def generate_pptx(title: str, slides_data: list[dict[str, Any]]) -> bytes:
    """
    生成 PowerPoint 文件

    Args:
        title: PPT 标题
        slides_data: 幻灯片数据列表，每个元素包含 {"title": str, "content": list[str]}

    Returns:
        .pptx 文件的二进制数据
    """
    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)

    # 标题页
    title_slide_layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(title_slide_layout)
    slide.shapes.title.text = title

    # 内容页
    for slide_data in slides_data:
        bullet_slide_layout = prs.slide_layouts[1]  # 标题 + 内容布局
        slide = prs.slides.add_slide(bullet_slide_layout)

        shapes = slide.shapes
        title_shape = shapes.title
        body_shape = shapes.placeholders[1]

        title_shape.text = slide_data.get("title", "")

        tf = body_shape.text_frame
        content_items = slide_data.get("content", [])

        for i, item in enumerate(content_items):
            if i == 0:
                p = tf.paragraphs[0]
            else:
                p = tf.add_paragraph()
            p.text = item
            p.level = 0
            p.font.size = Pt(18)

    # 保存到内存
    buffer = io.BytesIO()
    prs.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def generate_docx(title: str, sections: list[dict[str, Any]]) -> bytes:
    """
    生成 Word 文档

    Args:
        title: 文档标题
        sections: 章节数据列表，每个元素包含 {"heading": str, "paragraphs": list[str]}

    Returns:
        .docx 文件的二进制数据
    """
    doc = Document()

    # 添加标题
    heading = doc.add_heading(title, level=0)
    heading.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER

    # 添加章节
    for section in sections:
        section_heading = section.get("heading", "")
        if section_heading:
            doc.add_heading(section_heading, level=1)

        paragraphs = section.get("paragraphs", [])
        for para_text in paragraphs:
            p = doc.add_paragraph(para_text)
            p.style = 'Normal'

    # 保存到内存
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


def generate_chart(chart_type: str, data: dict[str, Any], title: str) -> bytes:
    """
    生成数据图表（PNG）

    Args:
        chart_type: 图表类型 (bar, line, pie)
        data: 图表数据 {"labels": [...], "values": [...]}
        title: 图表标题

    Returns:
        PNG 图片的二进制数据
    """
    plt.figure(figsize=(10, 6))
    plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    labels = data.get("labels", [])
    values = data.get("values", [])

    if chart_type == "bar":
        plt.bar(labels, values)
        plt.xlabel("类别")
        plt.ylabel("数值")
    elif chart_type == "line":
        plt.plot(labels, values, marker='o')
        plt.xlabel("类别")
        plt.ylabel("数值")
        plt.grid(True, alpha=0.3)
    elif chart_type == "pie":
        plt.pie(values, labels=labels, autopct='%1.1f%%')
    else:
        # 默认柱状图
        plt.bar(labels, values)

    plt.title(title, fontsize=14, fontweight='bold')
    plt.tight_layout()

    # 保存到内存
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buffer.seek(0)
    return buffer.getvalue()


async def generate_image(prompt: str) -> bytes:
    """
    调用图片生成 API 生成图片

    Args:
        prompt: 图片描述提示词

    Returns:
        图片的二进制数据
    """
    image_url = settings.image_api_url
    image_key = settings.image_api_key or settings.anthropic_api_key
    image_model = settings.image_model

    if not image_url:
        # 根据 anthropic_base_url 自动推断
        base = settings.anthropic_base_url.rstrip("/")
        if "/anthropic" in base:
            base = base.split("/anthropic")[0]
        image_url = f"{base}/v1/images/generations"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                image_url,
                headers={
                    "Authorization": f"Bearer {image_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": image_model,
                    "prompt": prompt,
                    "n": 1,
                    "size": "1024x1024",
                },
            )

        if resp.status_code == 200:
            result = resp.json()
            image_data_url = result.get("data", [{}])[0].get("url")

            if not image_data_url:
                raise ValueError("API 返回的图片 URL 为空")

            # 下载图片
            async with httpx.AsyncClient(timeout=60) as client:
                img_resp = await client.get(image_data_url)
                if img_resp.status_code == 200:
                    return img_resp.content
                else:
                    raise ValueError(f"下载图片失败: {img_resp.status_code}")
        else:
            logger.error("图片生成 API 错误: %s %s", resp.status_code, resp.text)
            raise ValueError(f"图片生成失败 ({resp.status_code}): {resp.text}")

    except Exception as e:
        logger.exception("调用图片生成 API 失败")
        raise
