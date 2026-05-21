from selenium import webdriver
from selenium.webdriver.edge.options import Options
import sys
import os
import time
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
        self.terminal.flush()
        self.log.flush()


sys.stdout = PrintToFile("输出日志.txt")


def get_resource_path(relative_path):
    """获取资源文件路径，兼容开发和打包环境"""
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


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
        browser_path = get_resource_path("driver/msedgedriver.exe")
        service = Service(executable_path=browser_path)
        browser = webdriver.Edge(options=edge_options, service=service)
    except Exception as e:
        print(f"❌ 浏览器初始化失败！请确保：")
        print(f"   1. 已关闭所有Edge浏览器窗口")
        print(f"   2. 已用命令启动Edge：msedge.exe --remote-debugging-port=9222")
        print(f"   3. driver/msedgedriver.exe 版本与你的Edge版本一致")
        raise e

    browser.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    browser.implicitly_wait(5)
    global action
    action = ActionChains(browser)
    print("✅ 浏览器初始化成功")
    return browser


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


def finish_work(driver, homework, option_elements=None):
    """完成单道题目"""
    global dep
    if not dep:
        print("⚠️  DeepSeek API未初始化，跳过本题")
        return

    title = homework["title"]
    print(f"\n📝 题目：{title}")

    try:
        # 调用AI获取答案
        answer = dep.dialogue(homework).strip()
        parsed_answer = parse_answer(answer)
        print(f"🤖 AI答案：{answer}")
        print(f"🔍 解析后：{parsed_answer}")

        # 判断题/单选题
        if "判断" in title or "单选" in title and option_elements:
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

        # 填空题
        elif "填空" in title:
            # 定位当前题目下的所有输入框
            input_boxes = driver.find_elements(By.XPATH, ".//div[contains(@class, 'marking_question_input')]//input | .//textarea")
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
            try:
                textarea = driver.find_element(By.TAG_NAME, "textarea")
                scroll_to_element(driver, textarea)
                textarea.clear()
                textarea.send_keys(answer)
                print("✅ 已填写简答题")
            except:
                print("⚠️  未找到简答题输入框")

    except Exception as e:
        print(f"❌ 答题失败：{str(e)[:100]}")  # 只打印前100个字符，避免日志过长

    time.sleep(0.5)  # 答题间隔，防止过快被检测


def get_all_titles(driver):
    """获取并处理所有题目"""
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

                    finish_work(driver, homework, option_elements)

                except Exception as e:
                    print(f"⚠️  跳过第{total_questions}题：{str(e)[:50]}")
                    continue

        except Exception as e:
            print(f"⚠️  跳过题型分组：{str(e)[:50]}")
            continue

    print(f"\n🎉 答题完成！共处理 {total_questions} 道题目")
    print("⚠️  请手动检查答案并点击提交按钮")


def get_api():
    """从文件加载DeepSeek API密钥"""
    global api, dep
    try:
        with open("想不通账号信息.txt", "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
            # 第5行（索引4）是API密钥，格式：api=xxx
            for line in lines:
                if line.startswith("api="):
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


if __name__ == "__main__":
    browser = None
    try:
        # 步骤1：初始化浏览器
        browser = init_browser()

        # 步骤2：加载API
        get_api()

        # 步骤3：开始答题
        get_all_titles(browser)

        # 保持浏览器打开，直到用户手动关闭
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