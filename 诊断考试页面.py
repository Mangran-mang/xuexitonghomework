"""诊断考试页面：只读抓取考试页面的 DOM 结构，定位答题脚本选择器失配问题。

用法：先在「考试模式 Edge」（调试端口 9222）里打开考试答题页面，然后运行：
    python 诊断考试页面.py
整个过程只读取页面内容，不点击、不填写、不提交，不影响答题。
结果保存到「考试页面结构_*.html」，控制台打印各选择器命中数量。
"""

import os
import sys

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service

BASE_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)

MAX_DEPTH = 3  # iframe 最多往下钻几层

# 考试脚本用到的选择器 + 常见的新旧版结构，逐一统计命中数
PROBE_SELECTORS = {
    "旧版-题型分组 marking_type": '//div[contains(@class,"marking_type")]',
    "旧版-题目 marking_question": '//div[contains(@class,"marking_question")]',
    "旧版-题目标题 marking_question_title": '//h3[contains(@class,"marking_question_title")]',
    "旧版-选项 marking_question_option": '//div[contains(@class,"marking_question_option")]',
    "旧版-填空输入 marking_question_input": '//*[contains(@class,"marking_question_input")]',
    "旧版-容器 fanyaMarking": '//*[@id="fanyaMarking"]',
    "作业版-题型区 submitForm": '//*[@id="submitForm"]',
    "新版-题块 TiMu": '//*[contains(@class,"TiMu")]',
    "新版-题干 fontLabel": '//*[contains(@class,"fontLabel")]',
    "字样-单选": '//*[contains(text(),"单选")]',
    "字样-多选": '//*[contains(text(),"多选")]',
    "字样-判断": '//*[contains(text(),"判断")]',
    "字样-填空": '//*[contains(text(),"填空")]',
    "输入框 input": "//input",
    "文本域 textarea": "//textarea",
    "UEditor富文本": '//*[contains(@id,"ueditor")]',
}

reports = []


def get_resource_path(relative_path: str) -> str:
    return os.path.join(BASE_DIR, relative_path)


def attach_browser():
    opts = Options()
    opts.debugger_address = "127.0.0.1:9222"
    service = Service(executable_path=get_resource_path("driver/msedgedriver.exe"))
    return webdriver.Edge(options=opts, service=service)


def walk(driver, indices, depth):
    driver.switch_to.default_content()
    for idx in indices:
        frames = driver.find_elements(By.XPATH, "//iframe | //frame")
        driver.switch_to.frame(frames[idx])

    # 当前层 URL（在 iframe 里拿不到时忽略）
    try:
        url = driver.execute_script("return location.href")[:90]
    except Exception:
        url = "?"

    label = "顶层页面" if not indices else "iframe:" + "/".join(str(i) for i in indices)
    result = {"位置": label, "层级": depth, "url": url, "命中": {}}

    for name, xpath in PROBE_SELECTORS.items():
        try:
            result["命中"][name] = len(driver.find_elements(By.XPATH, xpath))
        except Exception as e:
            result["命中"][name] = f"出错:{str(e)[:40]}"
    reports.append(result)

    # 落盘完整 HTML
    try:
        html = driver.execute_script("return document.documentElement.outerHTML")
        safe = "".join(c if c.isalnum() else "_" for c in label)[:60]
        filename = f"考试页面结构_{safe}.html"
        with open(os.path.join(BASE_DIR, filename), "w", encoding="utf-8") as f:
            f.write(html)
        hit_some = any(
            isinstance(v, int) and v > 0
            for k, v in result["命中"].items()
            if k.startswith("字样") or "题" in k or "容器" in k
        )
        print(f"{'    ' * depth}[{label}] {filename}" + ("   ← 疑似有题目！" if hit_some else ""))
    except Exception as e:
        print(f"{'    ' * depth}[{label}] 保存HTML失败：{str(e)[:60]}")

    if depth < MAX_DEPTH:
        driver.switch_to.default_content()
        for idx in indices:
            frames = driver.find_elements(By.XPATH, "//iframe | //frame")
            driver.switch_to.frame(frames[idx])
        try:
            frames = driver.find_elements(By.XPATH, "//iframe | //frame")
            count = len(frames)
        except Exception:
            count = 0
        for i in range(count):
            walk(driver, indices + [i], depth + 1)

    driver.switch_to.default_content()


if __name__ == "__main__":
    try:
        driver = attach_browser()
    except Exception as e:
        print(f"❌ 接管浏览器失败：{str(e)[:200]}")
        print("请先在配置面板点「启动考试模式 Edge」并打开考试页面。")
        sys.exit(1)

    print("✅ 已接管考试模式 Edge，开始只读检查各窗口…\n")
    try:
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            print(f"—— 窗口：{driver.title[:50]}  {driver.current_url[:80]}")
            walk(driver, [], 0)
    finally:
        driver.switch_to.default_content()

    print("\n══════════ 各层选择器命中统计 ══════════")
    for r in reports:
        hits = {k: v for k, v in r["命中"].items() if v != 0}
        print(f"\n◆ {r['位置']}  ({r.get('url', '')})")
        if not hits:
            print("   （全部为 0）")
        for k, v in hits.items():
            print(f"   {k}: {v}")
    print("\n完成：HTML 已存到项目目录的 考试页面结构_*.html；页面未被任何操作影响，可继续答题。")
