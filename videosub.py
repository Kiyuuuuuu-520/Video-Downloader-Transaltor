#!/usr/bin/env python3
"""
视频下载 + 转码 + 字幕生成
用法: python videosub.py
"""

import os
import subprocess
from pathlib import Path
from datetime import datetime
from google import genai

OUTPUT_DIR = Path("outputs")

SEPARATOR = "\n"          # 链接分隔符，改成 "," 或 "|" 等
WHISPER_MODEL = "small"    # tiny / base / small / medium / large

GOOGLE_API_KEY = "AIzaSyC8Gz3j0uxjYwZ7HzB0g2Sy-DAAk47M0bw"  # 填入你的 API key


def run(cmd, desc=""):
    print(f"  → {desc or ' '.join(cmd[:3])}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ 失败:\n{result.stderr[-500:]}")
        return False
    return True

def get_raw_title(url: str) -> str:
    """用 yt-dlp 获取原始标题"""
    result = subprocess.run(
        ["yt-dlp", "--get-title", "--no-playlist", url],
        capture_output=True, text=True
    )
    return result.stdout.strip() or "unknown"


def translate_titles(titles: list[str]) -> list[str]:
    """用 Gemini 把所有标题一次性翻译成中文"""
    client = genai.Client(api_key=GOOGLE_API_KEY)

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    prompt = (
        "请将以下视频标题翻译成简洁的中文，适合作为文件夹名称（不能含特殊字符）。\n"
        "每行格式严格为：序号. 中文标题\n"
        "不要添加任何解释或多余内容。\n\n"
        + numbered
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    zh_titles = []
    for line in response.text.strip().splitlines():
        line = line.strip()
        if ". " in line:
            zh = line.split(". ", 1)[1].strip()
        else:
            zh = line
        # 清理不能用于文件夹名的字符
        safe = "".join(c if c.isalnum() or c in " _-（）()【】" else "_" for c in zh)[:30].strip()
        zh_titles.append(safe)

    # 防御：如果 Gemini 返回行数对不上，fallback 用原标题
    if len(zh_titles) != len(titles):
        print("  ⚠ 翻译结果行数不匹配，使用原始标题")
        return [t[:30] for t in titles]

    return zh_titles


def process(url: str, idx: int, video_dir: Path):
    print(f"\n[{idx}] {url}")
    print(f"  输出到: {video_dir.name}")
 
 
    # 1. 下载
    orig = video_dir / "original"
    ok = run(
        ["yt-dlp", "-o", f"{orig}.%(ext)s", "--no-playlist", url],
        "yt-dlp 下载"
    )
    if not ok:
        return

    orig_files = [f for f in video_dir.glob("original.*") if f.suffix not in (".json", ".part")]
    if not orig_files:
        print("  ✗ 找不到下载文件")
        return
    orig_file = orig_files[0]
    print(f"  ✓ 原视频: {orig_file.name}")

    # 2. ffmpeg 转码 H.264
    h264 = video_dir / f"video_{idx}_h264.mp4"
    ok = run(
        ["ffmpeg", "-y", "-i", str(orig_file),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         str(h264)],
        "ffmpeg 转码 H.264"
    )
    if ok:
        print(f"  ✓ H.264: {h264.name}")

    # 3. 提取音频
    audio = video_dir / "audio.wav"
    run(
        ["ffmpeg", "-y", "-i", str(orig_file),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(audio)],
        "提取音频"
    )

    # 4. Whisper 字幕
    srt_out = video_dir / f"video_{idx}.srt"
    ok = run(
        ["whisper", str(audio),
         "--model", WHISPER_MODEL,
         "--output_format", "srt",
         "--output_dir", str(video_dir)],
        f"Whisper 识别 (model={WHISPER_MODEL})"
    )
    generated = video_dir / "audio.srt"
    if generated.exists():
        generated.rename(srt_out)
        print(f"  ✓ SRT: {srt_out.name}")
    else:
        print("  ✗ 字幕生成失败")



    # 5. Gemini 翻译 SRT 为中文
    if srt_out.exists() and GOOGLE_API_KEY:

        print(f"  → Gemini 翻译字幕为中文...")
        try:
            client = genai.Client(api_key=GOOGLE_API_KEY)
            srt_text = srt_out.read_text(encoding="utf-8")
            

            prompt = (
                "请将以下 SRT 字幕文件翻译成中文。"
                "保持 SRT 格式完全不变（序号、时间轴、换行），只翻译文本内容，不要添加任何解释。\n\n"
                "翻译要信达雅 适合短视频的 字幕"
                + srt_text
            )

            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=prompt
            )
            zh_srt = video_dir / "translated.srt"
            zh_srt.write_text(response.text, encoding="utf-8")
            print(f"  ✓ 中文字幕: {zh_srt.name}")
        except Exception as e:
            print(f"  ✗ 翻译失败: {e}")


def main():
    print("=" * 50)
    print("VideoSub — 视频下载 / 转码 / 字幕")
    print("=" * 50)

    print("\n粘贴视频链接（每行一个，输完按两次回车结束）:")
    lines = []
    while True:
        line = input().strip()
        if line == "":
            break
        lines.append(line)

    if not lines:
        print("没有输入链接，退出。")
        return

    urls = lines
    print(f"\n共 {len(urls)} 个链接")

    # 获取所有原始标题
    print("\n正在获取视频标题...")
    raw_titles = [get_raw_title(url) for url in urls]
    for i, t in enumerate(raw_titles, 1):
        print(f"  {i}. {t}")

    # Gemini 翻译所有标题
    print("\n正在翻译标题...")
    zh_titles = translate_titles(raw_titles)
    for i, t in enumerate(zh_titles, 1):
        print(f"  {i}. {t}")

    # 生成外层文件夹名
    timestamp = datetime.now().strftime("%Y%m%d")
    combined = "+".join(zh_titles)
    session_name = f"{timestamp}_{combined}"[:80]
    session_dir = OUTPUT_DIR / session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n输出文件夹: {session_name}")

    # 每个视频一个子文件夹
    for i, (url, zh_title) in enumerate(zip(urls, zh_titles), 1):
        video_dir = session_dir / f"{i}_{zh_title}"
        video_dir.mkdir(exist_ok=True)
        process(url, i, video_dir)

    print(f"\n完成！文件保存在 ./{session_dir}/")


if __name__ == "__main__":
    main()