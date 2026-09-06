from selenium import webdriver
from selenium.webdriver.edge.options import Options
import sys
import os
import re
import time
import subprocess
import zipfile
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
# 如果你没有ai_deepseek库，注释掉这行
import ai_deepseek

# 全局配置
question_bank = []
api = ""
dep = None  # 全局DeepSeek实例，只初始化一次
action = None  # 全局ActionChains实例

# 配置面板调用时加 --auto-exit：答完自动退出脚本（浏览器保持打开，不自动交卷）
AUTO_EXIT = "--auto-exit" in sys.argv
# --limit N：只答前 N 道题（调试用）
LIMIT = None
if "--limit" in sys.argv:
    try:
        LIMIT = int(sys.argv[sys.argv.index("--limit") + 1])
    except (ValueError, IndexError):
        pass

# 新版考试「整卷预览」页面的地址特征
PREVIEW_URL_MARK = "/exam/preview"


# 输出日志类（修复flush方法）
class PrintToFile:
    def __init__(self, file_name):
        self.terminal = sys.stdout
        self.log = open(file_name, "w", encoding="utf-8")

    def write(self, message):
        if self.terminal is not None:
            self.terminal.write(message)
        if self.log is not None:
            self.log.write(message)
            self.log.flush()  # 立即写入日志

    def flush(self):
        if self.terminal is not None:
            try:
                self.terminal.flush()
            except Exception:
                pass
        if self.log is not None:
            self.log.flush()


# 打包成 exe 后直接双击/控制台运行时，stdout 可能是 GBK 编码，
# 打印 emoji 会直接崩；强制重配为 UTF-8（对窗口模式等异常流做容错）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.stdout = PrintToFile("输出日志.txt")


def get_resource_path(relative_path):
    """获取资源文件路径，兼容开发和打包环境"""
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


# ─────────────────────────────────────────────
# Edge 驱动自动匹配（Edge 自动更新后无需手动换 exe）
# ─────────────────────────────────────────────
def get_local_edge_version() -> str:
    """读本机 Edge 浏览器版本号（注册表优先，安装目录名兜底）。"""
    try:
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Edge\BLBeacon") as key:
                    return winreg.QueryValueEx(key, "version")[0]
            except OSError:
                continue
    except Exception:
        pass
    # Edge 的安装目录本身就是按版本号命名的：Application/<版本>/msedge.exe
    for base in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application",
        r"C:\Program Files\Microsoft\Edge\Application",
    ):
        if os.path.isdir(base):
            versions = [
                d for d in os.listdir(base) if re.fullmatch(r"\d+(\.\d+){3}", d)
            ]
            if versions:
                return max(versions, key=lambda v: tuple(map(int, v.split("."))))
    raise OSError("读不到本机 Edge 版本号")


def get_driver_file_version(path: str) -> str:
    """读本地 msedgedriver.exe 的版本号，读不到返回空串。"""
    try:
        out = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=30
        )
        m = re.search(r"\d+(\.\d+){3}", (out.stdout or "") + (out.stderr or ""))
        return m.group(0) if m else ""
    except Exception:
        return ""


def download_driver(edge_version: str, target: str) -> bool:
    """下载指定版本的 msedgedriver 解压到 target；官方源失败再用国内镜像。"""
    try:
        import requests
    except ImportError:
        print("⚠️ 未安装 requests，无法自动下载驱动")
        return False
    urls = [
        f"https://msedgedriver.microsoft.com/{edge_version}/edgedriver_win64.zip",
        f"https://registry.npmmirror.com/-/binary/edgedriver/{edge_version}/edgedriver_win64.zip",
    ]
    zip_path = target + ".zip"
    for url in urls:
        try:
            print(f"⬇ 正在下载 msedgedriver {edge_version}：{url}")
            with requests.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(zip_path, "wb") as f:
                    for chunk in resp.iter_content(256 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            print(f"\r   下载进度 {done * 100 // total}%", end="", flush=True)
            print()
            with zipfile.ZipFile(zip_path) as z:
                name = next(n for n in z.namelist() if n.endswith("msedgedriver.exe"))
                z.extract(name, os.path.dirname(target))
                extracted = os.path.join(os.path.dirname(target), name)
            if os.path.abspath(extracted) != os.path.abspath(target):
                os.replace(extracted, target)
            print(f"✅ 驱动已更新：{target}")
            return True
        except Exception as e:
            print(f"\n⚠️ 下载失败：{str(e)[:100]}")
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
    return False


def resolve_driver_path() -> str:
    """本地 driver/msedgedriver.exe 版本与 Edge 匹配就直接用，不匹配就自动下载新版。"""
    local = get_resource_path("driver/msedgedriver.exe")
    try:
        edge_version = get_local_edge_version()
    except OSError as e:
        print(f"⚠️ {e}，直接使用本地驱动")
        if os.path.exists(local):
            return local
        raise FileNotFoundError(f"未找到 {local}")

    if os.path.exists(local):
        driver_version = get_driver_file_version(local)
        if driver_version.split(".")[0] == edge_version.split(".")[0]:
            print(f"✅ 本地驱动({driver_version}) 与 Edge({edge_version}) 版本匹配")
            return local
        print(f"⚠️ 本地驱动({driver_version or '版本未知'}) 与 Edge({edge_version}) 不匹配，尝试自动更新")

    if download_driver(edge_version, local):
        return local
    if os.path.exists(local):
        print("⚠️ 自动下载失败，继续使用本地驱动（注意版本可能不匹配）")
        return local
    raise FileNotFoundError(f"未找到 {local}，且自动下载失败")


def init_browser():
    """初始化浏览器，接管本地9222端口的Edge"""
    edge_options = Options()
    # 反爬配置
    edge_options.add_argument("--disable-blink-features=AutomationControlled")
    edge_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0")
    # 接管本地调试模式的Edge
    edge_options.debugger_address = "127.0.0.1:9222"
    # 关闭密码保存提示
    edge_options.add_argument("--disable-password-manager")
    edge_options.add_argument("--disable-save-password-bubble")

    try:
        service = Service(executable_path=resolve_driver_path())
        browser = webdriver.Edge(options=edge_options, service=service)
    except Exception as e:
        print(f"❌ 浏览器初始化失败！请确保：")
        print(f"   1. 已启动带调试端口的Edge（在配置面板里点「启动考试模式 Edge」，或手动执行）：")
        print(f"      msedge.exe --remote-debugging-port=9222 --user-data-dir=\"C:\\EdgeTest\"")
        print(f"   2. 已经在窗口里打开考试答题页面")
        print(f"   3. driver/msedgedriver.exe 版本与你的Edge版本一致")
        raise e

    # 反检测：隐藏 webdriver 标记；同一标签页重复定义会报错，包一层容错
    try:
        browser.execute_script(
            "try { Object.defineProperty(navigator,'webdriver',{get:()=>undefined}); }"
            " catch(e) {}"
        )
    except Exception:
        pass
    browser.implicitly_wait(5)
    global action
    action = ActionChains(browser)
    print("✅ 浏览器初始化成功")
    return browser


def get_api():
    """从文件加载DeepSeek API密钥（兼容 API: / api= 等写法）"""
    global api, dep
    try:
        with open(get_resource_path("想不通账号信息.txt"), "r", encoding="utf-8-sig") as f:
            for line in f.read().splitlines():
                if line[:4].lower() in ("api:", "api=", "api："):
                    api = line[4:].strip()
                    break

        if not api:
            print("⚠️  未在文件中找到API密钥")
            return

        # 全局只初始化一次DeepSeek
        dep = ai_deepseek.OpenDeepSeek(api)
        print(f"✅ DeepSeek API初始化成功")

    except FileNotFoundError:
        print("⚠️  未找到「想不通账号信息.txt」文件")
    except Exception as e:
        print(f"❌ API加载失败：{e}")


def scroll_to_element(driver, element):
    """滚动到元素位置，确保元素在视口内"""
    driver.execute_script("arguments[0].scrollIntoView({block: 'center', behavior: 'smooth'});", element)
    time.sleep(0.3)  # 等待滚动完成


def parse_answer(answer_text):
    """解析AI返回的答案，提取选项字母或内容"""
    answer_text = answer_text.strip().upper()
    # 处理常见的答案格式：A、B、C / ABC / 1. A / 正确 / 错误
    if "正确" in answer_text:
        return ["正确"]
    if "错误" in answer_text:
        return ["错误"]

    # 提取所有大写字母选项
    options = []
    for c in answer_text:
        if c in "ABCDEFG":
            options.append(c)
    return options if options else [answer_text]


# ═════════════════════════════════════════════
# 新版考试页面（整卷预览）：题目是顶层 li.questionLi 结构，
# 选项是可点击 div（onclick 调 saveSingleSelect/saveMultiSelect），
# 点完立即 AJAX 保存，答案值记录在隐藏域 #answer{qid} 里。
# ═════════════════════════════════════════════
def find_preview_window(driver):
    """返回已打开的整卷预览窗口句柄，没有返回 None。"""
    current = driver.current_window_handle
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if PREVIEW_URL_MARK in driver.current_url:
            return handle
    driver.switch_to.window(current)
    return None


def click_preview_button_if_found(driver):
    """在所有窗口（含一层 iframe）里找「整卷预览」按钮并点击。"""
    origin = driver.current_window_handle
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        for _ in range(2):  # 第1轮顶层，第2轮一层iframe
            try:
                candidates = driver.find_elements(
                    By.XPATH, "//*[normalize-space(text())='整卷预览']"
                )
                if candidates:
                    candidates[0].click()
                    return True
            except Exception:
                pass
            try:
                frames = driver.find_elements(By.XPATH, "//iframe | //frame")
                if frames:
                    driver.switch_to.frame(frames[0])
                    continue
            except Exception:
                pass
            break
        driver.switch_to.default_content()
    driver.switch_to.window(origin)
    return False


def ensure_preview_window(driver) -> bool:
    """确保停在整卷预览页面：已有就切换过去；没有就自动点「整卷预览」；
    点不到就提示手动点并轮询等待。返回 False 表示走旧版答题路径。"""
    handle = find_preview_window(driver)
    if handle:
        driver.switch_to.window(handle)
        print("✅ 检测到整卷预览页面，直接在上面答题")
        return True

    print("未发现整卷预览页面，尝试自动点击「整卷预览」按钮…")
    try:
        if click_preview_button_if_found(driver):
            for _ in range(20):
                time.sleep(1)
                handle = find_preview_window(driver)
                if handle:
                    driver.switch_to.window(handle)
                    print("✅ 已自动打开整卷预览页面")
                    return True
    except Exception as e:
        print(f"自动点击「整卷预览」出错：{str(e)[:80]}")

    print("⚠️ 请手动点击「整卷预览」，脚本会自动检测（最多等 90 秒）…")
    for _ in range(90):
        time.sleep(1)
        handle = find_preview_window(driver)
        if handle:
            driver.switch_to.window(handle)
            print("✅ 检测到整卷预览页面，开始答题")
            return True
    return False


def clean_title(raw: str) -> str:
    """去掉题干里的难度星号等噪声。"""
    title = re.sub(r"[（(]★+[)）]", "", raw)
    return re.sub(r"\s+", " ", title).strip()


def collect_preview_questions(driver) -> list:
    """扫描整卷预览页面，返回题目信息列表（只存数据，点击时按 qid 重新定位元素）。"""
    questions = []
    for li in driver.find_elements(By.XPATH, '//*[contains(@class,"questionLi")]'):
        try:
            raw_title = li.find_element(
                By.XPATH, './/*[contains(@class,"mark_name")]'
            ).text.replace("\n", " ").strip()
            qid = (li.get_attribute("data") or "").strip()
            qtype = ""
            for t in ("单选", "多选", "判断", "填空", "简答", "名词解释", "论述", "计算", "连线", "排序"):
                if t in raw_title:
                    qtype = t
                    break

            options = []
            for span in li.find_elements(
                By.XPATH, './/span[contains(@class,"num_option") and @data]'
            ):
                letter = (span.get_attribute("data") or "").strip()
                try:
                    row = span.find_element(By.XPATH, "..")
                    content = row.find_element(
                        By.XPATH, './/div[contains(@class,"answer_p")]'
                    ).text.strip()
                except Exception:
                    row, content = None, ""
                options.append({"letter": letter, "text": content})

            questions.append(
                {"qid": qid, "title": raw_title, "qtype": qtype or "未知", "options": options}
            )
        except Exception:
            continue
    return questions


def _question_el(driver, qid):
    """按 qid 重新定位题目元素，避免长答题过程中元素过期。"""
    return driver.find_element(By.ID, f"sigleQuestionDiv_{qid}")


def _read_saved_answer(driver, qid) -> str:
    """读页面隐藏域里保存的答案值（点击选项后由页面 JS 写入）。"""
    try:
        return driver.find_element(By.ID, f"answer{qid}").get_attribute("value") or ""
    except Exception:
        return ""


def _match_option(option: dict, parsed: list, qtype: str) -> bool:
    """判断某个选项是否该被选中。"""
    letter = option["letter"].upper()
    text = (option["text"] or "").strip()
    for a in parsed:
        a = a.strip().upper()
        if not a:
            continue
        if a == letter:
            return True
        if qtype == "判断":
            if a in ("正确", "对", "√", "T", "TRUE") and (
                "正确" in text or text in ("对", "√", "T")
            ):
                return True
            if a in ("错误", "错", "×", "X", "F", "FALSE") and (
                "错误" in text or text in ("错", "×", "F")
            ):
                return True
        # AI 直接回复选项内容的情况
        if len(a) > 1 and a in text.upper():
            return True
    return False


def _click_choice(driver, qid: str, letter: str) -> bool:
    """点击某个选项行（外层 div 带 onclick 保存事件）。"""
    try:
        el = _question_el(driver, qid)
        span = el.find_element(
            By.XPATH, f'.//span[contains(@class,"num_option") and @data="{letter}"]'
        )
        row = span.find_element(By.XPATH, "..")
        scroll_to_element(driver, row)
        try:
            row.click()
        except Exception:
            driver.execute_script("arguments[0].click();", row)
        time.sleep(0.4)
        return True
    except Exception as e:
        print(f"   点击选项 {letter} 失败：{str(e)[:80]}")
        return False


def _fill_choice(driver, q: dict, answer_text: str) -> bool:
    """按 AI 答案点选项；页面上的选项是"切换"语义（再点一次已选中的会取消），
    所以先读当前已保存的答案，只做必要的点击。"""
    parsed = parse_answer(answer_text)
    qid = q["qid"]
    multi = q["qtype"] == "多选"

    desired = []
    for opt in q["options"]:
        if _match_option(opt, parsed, q["qtype"]):
            desired.append(opt["letter"].upper())
    if not desired:
        print(f"   ❌ 没有匹配到可点击的选项（AI答案：{answer_text[:30]}）")
        return False
    if not multi:
        desired = desired[:1]

    current = _read_saved_answer(driver, qid).strip().upper()
    current_set = set(current)
    desired_set = set(desired)
    if current and current_set == desired_set:
        print(f"   ✅ 该题已选 {current}，与 AI 答案一致，无需操作")
        return True

    clicked = []
    if multi:
        # 多选：勾上缺的、取消多的（每次点击只切换对应选项）
        for opt in q["options"]:
            letter = opt["letter"].upper()
            if letter in desired_set and letter not in current_set:
                if _click_choice(driver, qid, letter):
                    clicked.append(letter)
            elif letter not in desired_set and letter in current_set:
                if _click_choice(driver, qid, letter):
                    clicked.append("(取消)" + letter)
    else:
        # 单选：点未选中的目标选项即可自动覆盖旧答案
        if _click_choice(driver, qid, desired[0]):
            clicked.append(desired[0])

    if not clicked:
        print(f"   ⚠️ 目标答案 {''.join(desired)} 与当前 {current or '空'} 不一致，但未执行点击")
        return False

    saved = _read_saved_answer(driver, qid).strip().upper()
    if saved:
        print(f"   ✅ 页面已保存答案：{saved}（本次点击：{' '.join(clicked)}）")
        return True
    print(f"   ⚠️ 已点击 {' '.join(clicked)}，但没读到保存值，请人工核对这道题")
    return True


def _fill_blank_or_essay_new(driver, q: dict, answer: str, multi_blank: bool) -> bool:
    """填空/简答：优先 UEditor 富文本，其次 textarea / contenteditable，最后调用页面保存。"""
    qid = q["qid"]
    try:
        el = _question_el(driver, qid)
    except Exception as e:
        print(f"   ❌ 定位题目失败：{str(e)[:60]}")
        return False

    parts = re.split(r"[和、，,;；]+|\s{2,}|\s和\s", answer) if multi_blank else [answer]
    parts = [p.strip() for p in parts if p.strip()] or [answer]
    filled = False

    # 1) UEditor 富文本（填空每空一个编辑器，简答一个）
    try:
        editor_frames = el.find_elements(
            By.XPATH, './/iframe[contains(@id,"ueditor")]|.//iframe[contains(@src,"ueditor")]'
        )
        for i, frame in enumerate(editor_frames):
            text = parts[i] if i < len(parts) else parts[-1]
            driver.switch_to.frame(frame)
            try:
                try:
                    body = driver.find_element(By.XPATH, "/html/body/p")
                except Exception:
                    body = driver.find_element(By.XPATH, "/html/body")
                body.click()
                time.sleep(0.2)
                body.clear()
                body.send_keys(text)
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", body
                )
                filled = True
            finally:
                driver.switch_to.default_content()
    except Exception:
        pass

    # 2) 普通 textarea
    if not filled:
        try:
            boxes = el.find_elements(By.XPATH, ".//textarea")
            for i, box in enumerate(boxes):
                text = parts[i] if i < len(parts) else parts[-1]
                scroll_to_element(driver, box)
                box.clear()
                box.send_keys(text)
                filled = True
        except Exception:
            pass

    # 3) contenteditable
    if not filled:
        try:
            boxes = el.find_elements(By.XPATH, ".//*[@contenteditable='true']")
            for i, box in enumerate(boxes):
                text = parts[i] if i < len(parts) else parts[-1]
                scroll_to_element(driver, box)
                driver.execute_script(
                    "arguments[0].innerText = arguments[1];"
                    "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));",
                    box, text,
                )
                filled = True
        except Exception:
            pass

    if not filled:
        print("   ❌ 没找到可填写的输入框/编辑器")
        return False

    # 调用页面自己的保存函数（所有题型的保存最终都走 submitForm）
    saved_ok = driver.execute_script(
        "var d = document.getElementById('sigleQuestionDiv_' + arguments[0]);"
        "if (d && typeof submitForm === 'function') { submitForm(true, d, function(){}); return true; }"
        "return false;",
        qid,
    )
    time.sleep(0.6)
    print(f"   ✅ 已填写：{answer[:40]}" + ("" if saved_ok else "（⚠️ 调用页面保存失败，交卷前请人工确认）"))
    return True


def answer_preview_page(driver):
    """新版整卷预览页面的主答题流程。"""
    print("\n🚀 开始自动答题（整卷预览页面）...")

    if not dep:
        print("⚠️  DeepSeek API未初始化，无法答题")
        return

    questions = collect_preview_questions(driver)
    if not questions:
        print("❌ 整卷预览页面上没找到题目（li.questionLi），请确认题目已显示")
        return

    type_count = {}
    for q in questions:
        type_count[q["qtype"]] = type_count.get(q["qtype"], 0) + 1
    print(f"共找到 {len(questions)} 道题：" + "，".join(f"{k} {v} 道" for k, v in type_count.items()))
    if LIMIT:
        questions = questions[:LIMIT]
        print(f"（--limit 生效：本次只答前 {LIMIT} 道）")

    ok = fail = 0
    for i, q in enumerate(questions, 1):
        raw_title = q["title"]
        print(f"\n📝 第{i}/{len(questions)}题（{q['qtype']}）：{clean_title(raw_title)[:60]}")
        try:
            el = _question_el(driver, q["qid"])

            # 简答/填空题带图片时 AI 答不了，跳过
            if q["qtype"] in ("简答", "论述", "名词解释", "填空") and el.find_elements(By.TAG_NAME, "img"):
                print("   ⚠️ 题目含图片，跳过（请人工作答）")
                fail += 1
                continue

            prompt_q = {
                "title": clean_title(raw_title),
                "options": (
                    [f"{o['letter']}. {o['text']}" for o in q["options"] if o["text"]]
                    if q["options"] else None
                ),
            }
            answer = dep.ask_one(prompt_q).strip()
            print(f"🤖 AI答案：{answer[:80]}")
            if not answer or answer.startswith("调用失败"):
                print("   ❌ AI未给出有效答案，跳过（请人工作答）")
                fail += 1
                continue

            if q["qtype"] in ("单选", "多选", "判断") or q["options"]:
                success = _fill_choice(driver, q, answer)
            elif q["qtype"] == "填空":
                success = _fill_blank_or_essay_new(driver, q, answer, multi_blank=True)
            else:
                success = _fill_blank_or_essay_new(driver, q, answer, multi_blank=False)

            if success:
                ok += 1
            else:
                fail += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"   ❌ 本题处理失败：{str(e)[:100]}")
            fail += 1

    print(f"\n🎉 答题完成：成功 {ok} 道，失败/跳过 {fail} 道，共 {len(questions)} 道")
    print("⚠️  请手动检查答案并自己点击提交按钮，脚本不会交卷")


# ═════════════════════════════════════════════
# 旧版考试/作业页面结构（marking_*），作为兜底保留
# ═════════════════════════════════════════════
def try_into_iframe(driver):
    """进入题目所在的iframe框架（必须调用！）"""
    try:
        iframe = WebDriverWait(driver, 15).until(EC.presence_of_element_located((
            By.ID, "frame_content")))
        driver.switch_to.frame(iframe)
        print("✅ 成功进入题目iframe框架")
        # 等待题目容器加载完成
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID,
                                                                        "fanyaMarking")))
    except Exception as e:
        print(f"❌ 进入iframe失败：{e}")
        print("⚠️  请确保你已经打开了考试页面，并且题目已经加载出来")
        raise e


def finish_work(driver, homework, option_elements=None, q_elem=None):
    """完成单道题目（旧版页面结构）"""
    global dep
    if not dep:
        print("⚠️  DeepSeek API未初始化，跳过本题")
        return

    title = homework["title"]
    print(f"\n📝 题目：{title}")

    try:
        # 调用AI获取答案（ask_one 内部带题型提示语）
        answer = dep.ask_one(homework).strip()
        parsed_answer = parse_answer(answer)
        print(f"🤖 AI答案：{answer}")
        print(f"🔍 解析后：{parsed_answer}")

        # 判断题/单选题
        if ("判断" in title or "单选" in title) and option_elements:
            for opt in option_elements:
                opt_text = opt.text.strip()
                # 匹配选项内容或选项字母
                if any(a in opt_text or opt_text.startswith(a) for a in
                       parsed_answer):
                    scroll_to_element(driver, opt)
                    # 点击选项的label（最稳定的点击方式）
                    label = opt.find_element(By.TAG_NAME, "label")
                    label.click()
                    print(f"✅ 已选择：{opt_text}")
                    break

        # 多选题
        elif "多选" in title and option_elements:
            selected = []
            for opt in option_elements:
                opt_text = opt.text.strip()
                if any(a in opt_text or opt_text.startswith(a) for a in
                       parsed_answer):
                    scroll_to_element(driver, opt)
                    label = opt.find_element(By.TAG_NAME, "label")
                    label.click()
                    selected.append(opt_text)
                    time.sleep(0.2)
            print(f"✅ 已选择：{selected}")

        # 填空题（限定在当前题目元素内查找输入框，避免填到别的题）
        elif "填空" in title:
            if q_elem is None:
                print("⚠️  未定位到题目元素，跳过填空题")
                return
            input_boxes = q_elem.find_elements(
                By.XPATH,
                ".//div[contains(@class, 'marking_question_input')]//input | .//textarea"
            )
            if input_boxes:
                # 简单处理：如果有多个空，用空格分割答案
                answers = answer.split()
                for i, box in enumerate(input_boxes):
                    if i < len(answers):
                        scroll_to_element(driver, box)
                        box.clear()
                        box.send_keys(answers[i])
                print(f"✅ 已填写：{answers[:len(input_boxes)]}")
            else:
                print("⚠️  未找到填空题输入框")

        # 简答题
        elif "简答" in title or "论述" in title:
            if q_elem is None:
                print("⚠️  未定位到题目元素，跳过简答题")
                return
            try:
                textarea = q_elem.find_element(By.TAG_NAME, "textarea")
                scroll_to_element(driver, textarea)
                textarea.clear()
                textarea.send_keys(answer)
                print("✅ 已填写简答题")
            except Exception:
                print("⚠️  未找到简答题输入框")

        else:
            print("⚠️  未识别的题型（或缺选项），跳过本题")

    except Exception as e:
        print(f"❌ 答题失败：{str(e)[:100]}")  # 只打印前100个字符，避免日志过长

    time.sleep(0.5)  # 答题间隔，防止过快被检测


def get_all_titles(driver):
    """获取并处理所有题目（旧版页面结构）"""
    print("\n🚀 开始自动答题...")

    # 先进入iframe（关键！）
    try_into_iframe(driver)

    # 获取所有题型分组
    type_groups = driver.find_elements(By.XPATH, '//div[contains(@class, "marking_type") and ./div/h2]')

    if not type_groups:
        print("❌ 未找到任何题目，请检查页面是否加载完成")
        return

    total_questions = 0
    for group in type_groups:
        try:
            # 获取题型名称
            type_title = group.find_element(By.XPATH, './div/h2').text.strip()
            print(f"\n📚 正在处理：{type_title}")

            # 获取该题型下的所有题目
            questions = group.find_elements(By.XPATH, './div[contains(@class, "marking_question")]')

            for q in questions:
                total_questions += 1
                try:
                    # 获取题干
                    title_elem = q.find_element(By.XPATH, './h3[contains(@class, "marking_question_title")]')
                    title_content = title_elem.text.replace("\n", "").strip()

                    # 获取选项（如果有）
                    option_elements = q.find_elements(By.XPATH, './div[contains(@class, "marking_question_option")]')

                    homework = {"title": title_content,
                        "options": [opt.text.strip() for opt in
                                    option_elements] if option_elements else None}

                    finish_work(driver, homework, option_elements, q)

                except Exception as e:
                    print(f"⚠️  跳过第{total_questions}题：{str(e)[:50]}")
                    continue

        except Exception as e:
            print(f"⚠️  跳过题型分组：{str(e)[:50]}")
            continue

    print(f"\n🎉 答题完成！共处理 {total_questions} 道题目")
    print("⚠️  请手动检查答案并点击提交按钮")


if __name__ == "__main__":
    browser = None
    try:
        # 步骤1：加载API
        get_api()

        # 步骤2：初始化浏览器（接管调试模式的Edge）
        browser = init_browser()

        # 步骤3：新版考试 → 确保停在整卷预览页面；否则按旧版结构兜底
        if ensure_preview_window(browser):
            answer_preview_page(browser)
        else:
            print("未进入整卷预览页面，尝试按旧版考试页面结构答题…")
            get_all_titles(browser)

        if AUTO_EXIT:
            print("\n✅ 答题流程结束，浏览器保持打开，请手动检查答案并提交")
        else:
            print("\n⏳ 浏览器将保持打开状态，按Ctrl+C退出程序")
            while True:
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n👋 用户手动退出程序")
    except Exception as e:
        print(f"\n❌ 程序运行出错：{e}")
    finally:
        # 不自动关闭浏览器，让用户手动提交
        # if browser:
        #     browser.quit()
        #     print("浏览器已关闭")
        pass
