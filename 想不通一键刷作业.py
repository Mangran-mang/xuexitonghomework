import sys
import os
import re
import time
import subprocess
import zipfile

from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By

import ai_deepseek

# ─────────────────────────────────────────────
# 全局状态
# ─────────────────────────────────────────────
question_bank: list[dict] = []
api: str = ""


# ─────────────────────────────────────────────
# 自定义异常（保留，供外部捕获用）
# ─────────────────────────────────────────────
class FileFindException(Exception):
    """填空题查找异常"""
    def __str__(self):
        return "填空题查找过程出错"


class FileFinishException(Exception):
    """填空题完成异常"""
    def __str__(self):
        return "填空题完成过程出错"


# ─────────────────────────────────────────────
# 同时输出到终端和日志文件
# ─────────────────────────────────────────────
class TeeOutput:
    def __init__(self, file_name: str):
        self.terminal = sys.stdout
        self.log = open(file_name, "w", encoding="utf-8")

    def write(self, message: str):
        if self.terminal:
            self.terminal.write(message)
        if self.log:
            self.log.write(message)

    def flush(self):
        if self.log:
            self.log.flush()


# 打包成 exe 后直接双击/控制台运行时，stdout 可能是 GBK 编码，
# 打印 emoji 会直接崩；强制重配为 UTF-8（对窗口模式等异常流做容错）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.stdout = TeeOutput("输出日志.txt")


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
def get_resource_path(relative_path: str) -> str:
    """兼容开发环境和 PyInstaller 打包环境的路径解析。"""
    base = (
        os.path.dirname(sys.executable)
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__))
    )
    return os.path.join(base, relative_path)


def wait(driver, xpath: str, timeout: float = 6):
    """快捷等待单个元素出现。"""
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.XPATH, xpath))
    )


def wait_all(driver, xpath: str, timeout: float = 6):
    """快捷等待多个元素出现。"""
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_all_elements_located((By.XPATH, xpath))
    )


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


def init_browser() -> webdriver.Edge:
    opts = Options()
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
    )
    opts.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })

    service = Service(executable_path=resolve_driver_path())
    browser = webdriver.Edge(options=opts, service=service)
    browser.execute_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    )
    browser.implicitly_wait(10)
    return browser


# ─────────────────────────────────────────────
# 读取账号信息
# ─────────────────────────────────────────────
def get_user_data() -> tuple[str, str, list[str]]:
    with open(get_resource_path("想不通账号信息.txt"), "r", encoding="utf-8") as f:
        lines = f.readlines()

    zhanghao = lines[1].strip()[3:]
    mima     = lines[2].strip()[3:]
    courses  = lines[3].strip()[7:].split(",")
    deepseek_api = lines[4].strip()[4:]

    global api
    api = deepseek_api
    print(f"待刷课程：{courses}")
    return zhanghao, mima, courses


# ─────────────────────────────────────────────
# 登录 + 遍历课程
# ─────────────────────────────────────────────
def visit_target_page(browser, zhanghao: str, mima: str, course_name_list: list[str]):
    try:
        wait(browser, '//*[@id="phone"]').send_keys(zhanghao)
        wait(browser, '//*[@id="pwd"]').send_keys(mima)
        wait(browser, '//*[@id="loginBtn"]').click()
        time.sleep(1)

        print("已进入个人主页")
        course_iframe = wait(browser, '//*[@id="frame_content"]', timeout=8)
        browser.switch_to.frame(course_iframe)
        time.sleep(1)

        # 检测旧版 / 新版切换按钮
        try:
            btn = WebDriverWait(browser, 0.5).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="divbox"]/div/div/div[1]/a'))
            )
            if "体验新版" in btn.text:
                btn.click()
                print("已切换到新版")
                time.sleep(1.5)
        except Exception:
            pass

        remaining = course_name_list.copy()
        stuck = 0
        while remaining:
            course_cards = browser.find_elements(
                By.XPATH, '//*[@id="stuNormalCourseListDiv"]/div'
            )
            if not course_cards:
                # 不在个人空间页（比如上一门课出错后停留位置不对）：回个人空间重试
                stuck += 1
                print(f"个人空间课程列表未加载（第{stuck}次）")
                if stuck >= 3:
                    print("❌ 找不到课程列表，退出。请检查登录状态或课程名是否正确。")
                    return
                browser.switch_to.default_content()
                browser.get("https://i.chaoxing.com/base")
                time.sleep(2)
                course_iframe = wait(browser, '//*[@id="frame_content"]', timeout=8)
                browser.switch_to.frame(course_iframe)
                continue
            stuck = 0
            for card in course_cards:
                try:
                    name       = card.find_element(By.XPATH, "./div[2]/h3").text
                    target_url = card.find_element(By.XPATH, "./div[2]/h3/a").get_attribute("href")

                    matched = next((t for t in remaining if t in name), None)
                    if not matched:
                        continue

                    browser.get(target_url)
                    print(f"已进入《{name}》课程页")
                    time.sleep(1.5)

                    try:
                        goto_home_work(browser, matched)
                    except Exception as e:
                        print(f"《{name}》处理出错（跳过该课程）：{e}")
                    finally:
                        remaining.remove(matched)
                        # 无论成败都直接回个人空间，下一门课要在这里找课程卡片
                        browser.switch_to.default_content()
                        browser.get("https://i.chaoxing.com/base")
                        time.sleep(1.5)

                    if remaining:
                        print(f"还有待刷课程：{remaining}")
                    course_iframe = wait(browser, '//*[@id="frame_content"]', timeout=8)
                    browser.switch_to.frame(course_iframe)
                    break
                except Exception:
                    continue

        print("所有课程处理完成，如有遗漏请检查课程名。")

    except Exception as e:
        print(f"找目标页面过程失败：{e}")


# ─────────────────────────────────────────────
# 进入课程作业列表，循环处理未交作业
# ─────────────────────────────────────────────
def goto_home_work(driver, course_name: str):
    """循环处理课程里所有未交作业。
    每轮都重新点"作业"选项卡再进列表：处理完一个作业回来页面会被刷新，
    选项卡状态会重置，只在循环外点一次会导致第二轮找不到 iframe 而中断。"""
    main_window = driver.current_window_handle
    tried_names = set()  # 处理过的作业名，防止提交后状态没变化导致死循环
    done_count = 0

    while True:
        # 回到课程页顶层，重新点"作业"选项卡
        try:
            driver.switch_to.default_content()
            tabs = wait_all(driver, "/html/body/div[1]/div[3]/div[1]/div/ul/li")
            for tab in tabs:
                if tab.get_attribute("dataname") == "zy":
                    tab.click()
                    break
            time.sleep(1)

            frame = wait(driver, '//*[@id="frame_content-zy"]')
            driver.switch_to.frame(frame)
            print("已进入作业列表 iframe")
        except Exception as e:
            print(f"《{course_name}》重新进入作业列表失败：{e}")
            break

        # 检测是否无作业 / 读取作业列表
        try:
            empty_div = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.XPATH, '/html/body/div[2]/div/div/div[2]/div[2]'))
            )
            if "暂无作业" in empty_div.text:
                print(f"《{course_name}》没有作业")
                break

            hw_list = WebDriverWait(driver, 3).until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, '/html/body/div[2]/div/div/div[2]/div[2]/ul/li')
                )
            )
        except Exception as e:
            print(f"《{course_name}》读取作业列表失败：{e}")
            break

        print(f"《{course_name}》作业数：{len(hw_list)}")
        time.sleep(0.5)

        processed = False
        for hw in hw_list:
            try:
                hw_name   = hw.find_element(By.XPATH, "./div[2]/p[1]").text
                hw_status = hw.find_element(By.XPATH, "./div[2]/p[2]").text

                if "实验" in hw_name or "报告" in hw_name:
                    print(f"跳过实验/报告作业：{hw_name}")
                    continue

                if "未交" not in hw_status:
                    continue
                if hw_name in tried_names:
                    continue
                tried_names.add(hw_name)

                print(f"开始处理作业：{hw_name}（{hw_status}）")
                hw.click()
                WebDriverWait(driver, 3).until(lambda d: len(d.window_handles) > 1)
                driver.switch_to.window(driver.window_handles[-1])

                try:
                    get_homework(driver)
                    done_count += 1
                except Exception as e:
                    print(f"处理作业出现问题：{e}")

                # 无论成败都把作业标签页关干净，回到课程页主窗口
                try:
                    for handle in driver.window_handles:
                        if handle != main_window:
                            driver.switch_to.window(handle)
                            driver.close()
                    driver.switch_to.window(main_window)
                except Exception as e:
                    print(f"清理作业窗口出错：{e}")
                    try:
                        driver.switch_to.window(main_window)
                    except Exception:
                        pass

                try:
                    driver.refresh()  # 刷新课程页，让作业状态更新
                    time.sleep(0.5)
                except Exception:
                    pass
                time.sleep(1)
                processed = True
                break
            except Exception:
                continue

        if not processed:
            break

    print(f"《{course_name}》处理完毕，本次完成 {done_count} 个作业")


# ─────────────────────────────────────────────
# 收集题目
# ─────────────────────────────────────────────
TITLE_TYPES = ["判断题", "单选题", "多选题", "填空题", "简答题"]


def get_homework(driver):
    """遍历页面上所有题型，把题目收集到 question_bank，再调用 finish_homework。"""
    type_sections = driver.find_elements(By.XPATH, '//*[@id="submitForm"]/div')
    homework: list[dict] = []

    for section in type_sections:
        section_type = section.find_element(By.XPATH, "./h2").text
        print(f"收集题型：{section_type}")

        has_options = any(t in section_type for t in ["单选题", "多选题"])

        for item in section.find_elements(By.XPATH, "./div"):
            try:
                title = item.find_element(By.XPATH, "./h3").text.replace("\n", "")
                if has_options:
                    options = item.find_element(By.XPATH, "./div[2]").text.replace("\n", ":")
                    homework.append({"title": title, "options": options})
                else:
                    homework.append({"title": title, "options": None})
            except Exception:
                pass

    if not homework:
        print("未找到题目")
        return

    global question_bank
    question_bank = homework
    print(f"共收集到 {len(homework)} 道题")

    finish_homework(driver)

    driver.close()
    tabs = driver.window_handles
    if tabs:
        driver.switch_to.window(tabs[0])
    else:
        print("没有其他窗口，发生严重错误")


# ─────────────────────────────────────────────
# 填写答案
# ─────────────────────────────────────────────
def finish_homework(driver):
    if not api:
        raise Exception("未找到 DeepSeek 的 API Key")

    dep = ai_deepseek.OpenDeepSeek(api)
    answer_list: list[str] = dep.dialogue(question_bank)
    question_bank.clear()

    type_sections = driver.find_elements(By.XPATH, '//*[@id="submitForm"]/div')

    for section in type_sections:
        section_type = section.find_element(By.XPATH, "./h2").text
        print(f"填写题型：{section_type}")
        items = section.find_elements(By.XPATH, "./div")

        for item in items:
            # 只处理有 h3 的真正题目 div，跳过分数标注等多余 div，
            # 与 get_homework 的过滤逻辑保持一致
            try:
                item.find_element(By.XPATH, "./h3")
            except Exception:
                continue

            if not answer_list:
                print("警告：答案列表已耗尽，跳过剩余题目")
                break

            if "单选题" in section_type:
                _fill_single_choice(driver, item, answer_list)
            elif "多选题" in section_type:
                _fill_multi_choice(driver, item, answer_list)
            elif "填空题" in section_type:
                _fill_blank(driver, item, answer_list)
            elif "判断题" in section_type:
                _fill_judge(driver, item, answer_list)
            elif "简答题" in section_type:
                _fill_essay(driver, item, answer_list)

    # 提交
    submit_btn = driver.find_element(By.XPATH, '//*[@id="submitFocus"]/a[2]')
    submit_btn.click()
    time.sleep(1)
    driver.find_element(By.XPATH, '//*[@id="popok"]').click()
    time.sleep(1)


# ─────────────────────────────────────────────
# 各题型填写函数
# ─────────────────────────────────────────────
def _read_editor_text(driver) -> str:
    """读取当前 iframe 里编辑器的纯文本内容（用于写入校验）。"""
    try:
        return driver.execute_script(
            "var b = document.body; return b ? (b.innerText || b.textContent || '') : '';"
        )
    except Exception:
        return ""


def _content_matches(expected: str, actual: str) -> bool:
    """忽略所有空白字符后，检查期望内容是否真的写进了编辑器。"""
    norm = lambda s: re.sub(r"\s+", "", s or "")
    return bool(norm(expected)) and norm(expected) in norm(actual)
def _fill_single_choice(driver, item, answer_list: list):
    answer = answer_list[0]
    try:
        for option in item.find_elements(By.XPATH, "./div[2]/div"):
            if option.text and option.text[0] == answer:
                ActionChains(driver).move_to_element(option).click().perform()
                answer_list.pop(0)
                time.sleep(0.5)
                return
        # 未匹配到选项也消费掉答案，防止错位
        print(f"单选题未匹配到答案「{answer}」，已跳过")
        answer_list.pop(0)
    except Exception as e:
        print(f"单选题出错：{e}")
        answer_list.pop(0)


def _fill_multi_choice(driver, item, answer_list: list):
    answer = answer_list[0]
    try:
        options = item.find_elements(By.XPATH, "./div[2]/div")
        for option in options:
            if option.text and option.text[0] in answer:
                ActionChains(driver).move_to_element(option).click().perform()
                time.sleep(0.5)
        answer_list.pop(0)
    except Exception as e:
        print(f"多选题出错：{e}")
        answer_list.pop(0)


def _fill_blank(driver, item, answer_list: list):
    """填空题：支持单空和多空。每空填完都读回内容校验，失败用 JS 兜底重写。"""
    raw_answer = answer_list.pop(0)

    # 拆分多空答案（常见分隔符）
    separators = ("和", "、", "·", "，", ",", " ")
    for sep in separators:
        if sep in raw_answer:
            parts = [p.strip() for p in raw_answer.split(sep) if p.strip()]
            if len(parts) > 1:
                break
    else:
        parts = [raw_answer]

    print(f"  填空答案：{raw_answer} → {parts}")

    try:
        vacancies = item.find_elements(By.XPATH, "./div[2]/div")
        print(f"  找到空位数：{len(vacancies)}")

        if not vacancies:
            print("  ⚠ 未找到填空输入框，跳过")
            return

        for i, vacancy in enumerate(vacancies):
            fill_text = parts[i] if i < len(parts) else parts[-1]
            print(f"  第{i+1}空填入：{fill_text}")
            try:
                iframe = vacancy.find_element(By.XPATH, ".//iframe")
                driver.switch_to.frame(iframe)

                # 填空题的富文本编辑器：尝试多种定位方式
                # 先尝试 p 标签（最常见），不行就操作 body 本身
                try:
                    inp = driver.find_element(By.XPATH, "/html/body/p")
                except Exception:
                    inp = driver.find_element(By.XPATH, "/html/body")

                # 先点击激活编辑器，再清空并键入
                inp.click()
                time.sleep(0.2)
                inp.clear()
                inp.send_keys(fill_text)
                # 触发 input 事件让编辑器感知内容变化
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));", inp
                )
                time.sleep(0.5)

                # 校验：读回内容确认真的写进去了
                if not _content_matches(fill_text, _read_editor_text(driver)):
                    print(f"  ⚠ 第{i+1}空键入后编辑器为空，改用 JS 直接写入…")
                    driver.execute_script(
                        "document.body.innerHTML = '<p>' + arguments[0].replace(/\\n/g, '<br>') + '</p>';",
                        fill_text,
                    )
                    driver.execute_script(
                        "document.body.dispatchEvent(new Event('input',{bubbles:true}));"
                    )
                    time.sleep(0.5)

                if _content_matches(fill_text, _read_editor_text(driver)):
                    print(f"  ✅ 第{i+1}空校验通过")
                else:
                    print(
                        f"  ❌ 第{i+1}空写入校验失败，编辑器当前内容：{_read_editor_text(driver)[:30]!r}"
                    )
            except Exception as e:
                print(f"  填空第 {i+1} 空出错：{e}")
            finally:
                driver.switch_to.default_content()
    except Exception as e:
        print(f"填空题整体出错：{e}")
        driver.switch_to.default_content()


def _fill_judge(driver, item, answer_list: list):
    answer = answer_list[0]
    try:
        for option in item.find_elements(By.XPATH, "./div[2]/div"):
            if answer in option.text:
                ActionChains(driver).move_to_element(option).click().perform()
                answer_list.pop(0)
                time.sleep(0.5)
                return
        print(f"判断题未匹配到答案「{answer}」，已跳过")
        answer_list.pop(0)
    except Exception as e:
        print(f"判断题出错：{e}")
        answer_list.pop(0)


def _fill_essay(driver, item, answer_list: list):
    """简答题：写入后读回编辑器内容校验，失败用 JS 兜底重写。"""
    answer = answer_list.pop(0)
    try:
        # 检测有图片则跳过
        if item.find_elements(By.TAG_NAME, "img"):
            print("简答题含图片，跳过")
            return

        # 使用相对 XPath，避免跨题定位错误
        essay_iframe = item.find_element(By.XPATH, './/*[contains(@id,"ueditor")]')
        driver.switch_to.frame(essay_iframe)

        try:
            inp = driver.find_element(By.XPATH, "/html/body/p")
        except Exception:
            inp = driver.find_element(By.XPATH, "/html/body")

        # 先点击激活编辑器，再清空并键入
        try:
            inp.click()
            time.sleep(0.2)
            inp.clear()
        except Exception:
            pass
        inp.send_keys(answer)
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", inp
        )
        time.sleep(0.5)

        # 校验：读回内容确认真的写进去了
        if not _content_matches(answer, _read_editor_text(driver)):
            print("  ⚠ 键入后编辑器为空，改用 JS 直接写入…")
            driver.execute_script(
                "document.body.innerHTML = '<p>' + arguments[0].replace(/\\n/g, '<br>') + '</p>';",
                answer,
            )
            driver.execute_script(
                "document.body.dispatchEvent(new Event('input',{bubbles:true}));"
            )
            time.sleep(0.5)

        if _content_matches(answer, _read_editor_text(driver)):
            print(f"  ✅ 简答题校验通过，编辑器内容：{_read_editor_text(driver)[:50]}")
        else:
            print(
                f"  ❌ 简答题写入校验失败，编辑器当前内容：{_read_editor_text(driver)[:50]!r}"
            )
    except Exception as e:
        print(f"简答题出错：{e}")
    finally:
        driver.switch_to.default_content()


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    browser = init_browser()
    try:
        browser.get(
            "https://passport2.chaoxing.com/login"
            "?fid=12&refer=http%3A%2F%2Fi.chaoxing.com%2Fbase%3Ft%3D1771763723377&space=2"
        )
        visit_target_page(browser, *get_user_data())
        time.sleep(3)
    finally:
        browser.quit()
