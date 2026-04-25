from selenium import webdriver
from selenium.webdriver.edge.options import Options

import sys
import os

import time
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.service import Service
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
import ai_deepseek

question_bank = []
api = ''


def get_resource_path(relative_path):
    # 直接读取exe所在的当前目录，不管是开发环境还是打包环境
    if getattr(sys, 'frozen', False):
        # 打包后：exe所在的目录
        base_path = os.path.dirname(sys.executable)
    else:
        # 开发时：当前脚本所在的目录
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)
# 初始化驱动
def init_browser():
    edge_options = Options()
    edge_options.add_experimental_option("excludeSwitches",["enable-automation"])# 隐藏自动化特征
    edge_options.add_experimental_option("useAutomationExtension",False)# 隐藏自动化特征
    edge_options.add_argument("--disable-blink-features=AutomationControlled")# 隐藏自动化特征
    edge_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0")
    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False
    }
    browser_path = get_resource_path("driver/msedgedriver.exe")
    service = Service(executable_path=browser_path)

    edge_options.add_experimental_option("prefs", prefs)
    browser = webdriver.Edge(options=edge_options,service=service)
    browser.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")# 隐藏自动化特征
    browser.implicitly_wait(10)
    return browser

def visit_target_page(browser,zhanghao1,mima1,course_name_list):
    try:
        zhanghao = WebDriverWait(browser, 10).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="phone"]'))
        )
        zhanghao.send_keys(zhanghao1)

        mima = WebDriverWait(browser, 10).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="pwd"]'))
        )
        mima.send_keys(mima1)

        WebDriverWait(browser, 10).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="loginBtn"]'))
        ).click()

        time.sleep(1.5)
        # 此时已进入个人空间
        # 先获取所有课程名
        course_iframe = WebDriverWait(browser,8).until(
            EC.presence_of_element_located((By.XPATH,'//*[@id="frame_content"]'))
        )
        browser.switch_to.frame(course_iframe)
        time.sleep(2)
        remaining_courses = course_name_list.copy()
        while remaining_courses:
            myStudy_course_list = browser.find_elements(By.XPATH, '//*[@id="stuNormalCourseListDiv"]/div')  # 直接获取所有具体课程
            for course in myStudy_course_list:
                try:
                    name = course.find_element(By.XPATH, './div[2]/h3').text
                    target_url = course.find_element(By.XPATH, './div[2]/h3/a').get_attribute('href')
                    # 检查是否是待刷课程
                    for target in remaining_courses:
                        if target in name:
                            browser.get(target_url)
                            print(f"已进入{name}课程页")
                            time.sleep(1.5)
                            goto_home_work(browser, target)
                            remaining_courses.remove(target)
                            time.sleep(1)
                            print("切换到任务界面等待结束1")
                            browser.back()
                            if browser.title != "个人空间":
                                browser.back()
                            time.sleep(1)
                            if remaining_courses:
                                print("还有待刷课程{}".format(remaining_courses))
                            browser.switch_to.frame(course_iframe)
                            break
                except Exception:
                    continue
        print("所有课程已全部处理完成,goodbye")
    except Exception as e:
        print(f"找目标页面过程失败：{e}")


def goto_home_work(driver,course_name):# 学习通具体作业解密没有嵌套
    WebDriverWait(driver,6).until(
        EC.presence_of_element_located((By.XPATH,'//*[@id="nav_18255"]/a'))# 点击作业选项
    ).click()
    main_window = driver.current_window_handle
    while True:
        # print("执行到这里1")
        frame_content = WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.XPATH, '//*[@id="frame_content-zy"]'))  # 进入iframe
        )
        driver.switch_to.frame(frame_content)
        homework_list = WebDriverWait(driver, 3).until(
            EC.presence_of_all_elements_located((By.XPATH, '/html/body/div[2]/div/div/div[2]/div[2]/ul/li'))
        )
        # print("执行到这里2")
        # if homework_list:
        #     print("成功获取作业列表")
        # else:
        #     print("获取作业列表失败")
        processed = False
        # 拿到全作业列表后，遍历拿到它们的名字和完成状况，并排除实验作业
        for homework in homework_list:
            try:
                homework_name = homework.find_element(By.XPATH, './div[2]/p[1]').text
                homework_status = homework.find_element(By.XPATH, './div[2]/p[2]').text

                if "实验" in homework_name:
                    print("已跳过实验作业")
                    continue
                if "未" in homework_status:
                    # 处理未完成作业
                    homework.click()
                    # 切换新窗口
                    WebDriverWait(driver, 3).until(lambda d: len(driver.window_handles) > 1)
                    driver.switch_to.window(driver.window_handles[-1])
                    try:
                        get_homework(driver)
                        time.sleep(0.5)
                        driver.refresh()
                        time.sleep(0.5)
                    except Exception as e:
                        print(f"处理作业出现问题：{e}")
                    # driver.close()
                    # driver.switch_to.window(main_window)
                    time.sleep(1)
                    processed = True
                    break
            except Exception:
                continue
            # 没有未完成作业了，退出循环
        if not processed:
            break
    print("已处理完毕《{}》的所有作业".format(course_name))
    driver.switch_to.default_content()
    driver.back()
    driver.back()


def get_homework(driver):
    all_titles = driver.find_elements(By.XPATH,'//*[@id="submitForm"]/div')# 储存所有题型
    title_type_list = ["判断题","单选题","多选题","填空题","简答题"]
    homework = []
    for i in all_titles:# i是某个题型的所有题
        title_type = i.find_element(By.XPATH,'./h2')
        print("正在处理的题型是{}".format(title_type.text))
        if title_type_list[0] in title_type.text:# 判断题
            for i_child in i.find_elements(By.XPATH,'./div'):# 找到所有具体题目外部标签
                try:
                    full_text = i_child.find_element(By.XPATH,'./h3').text.replace("\n", "")
                    homework.append({
                        "title":full_text,
                        "options":None})
                except Exception:
                    pass
        elif title_type_list[1] in title_type.text:# 单选题
            for i_child in i.find_elements(By.XPATH,'./div'):# 这里遍历的是单个类型的所有题目
                try:
                    title_content = i_child.find_element(By.XPATH,'./h3').text.replace("\n", "")
                    title_options = i_child.find_element(By.XPATH,'./div[2]').text.replace("\n", ":")
                    homework.append({
                        "title": title_content,
                        "options": title_options})
                except Exception:
                    pass
        elif title_type_list[2] in title_type.text:# 多选题
            for i_child in i.find_elements(By.XPATH,'./div'):
                try:
                    title_content = i_child.find_element(By.XPATH,'./h3').text.replace("\n", "")
                    title_options = i_child.find_element(By.XPATH,'./div[2]').text.replace("\n", ":")
                    homework.append({
                        "title": title_content,
                        "options": title_options})
                except Exception:
                    pass
        elif title_type_list[3] in title_type.text:# 填空题
            for i_child in i.find_elements(By.XPATH,'./div'):
                try:
                    title_content = i_child.find_element(By.XPATH,'./h3').text.replace("\n", "")
                    homework.append({
                        "title": title_content,
                        "options": None})
                except Exception:
                    pass
        else:
            for i_child in i.find_elements(By.XPATH, './div'):# 简答题
                try:
                    title_content = i_child.find_element(By.XPATH,'./h3').text.replace("\n", "")
                    homework.append({
                        "title": title_content,
                        "options": None})
                except Exception:
                    pass
    if homework:
        global question_bank
        print(homework)
        question_bank = homework
        finish_homework(driver)# 完成刷作业逻辑
        driver.close()
        tabs = driver.window_handles
        if tabs:
            driver.switch_to.window(tabs[0])
            # 这里回到作业列表界面
        else:
            print("没有其他窗口,发生严重错误")
        # 跳转逻辑
        # 巴拉巴拉
    else:
        print("未找到题目")

def get_user_data():
    with open("学习通账号信息.txt","r",encoding="utf-8") as f:
        try:
            all_data = f.readlines()
            zhanghao1 = all_data[1].strip()[3:]
            mima1 = all_data[2].strip()[3:]
            course_list = all_data[3].strip()[7:].split(",")
            DeepSeekApi = all_data[4].strip()[4:]
            global api
            api = DeepSeekApi
            print("你要刷的课程为{}".format(course_list))
        except Exception as e:
            print(f"获取用户信息出现问题：{e}")
    return zhanghao1,mima1,course_list

def finish_homework(driver):
    if api:
        dep = ai_deepseek.OpenDeepSeek(api)
        answer_list = dep.dialogue(question_bank)# ai返回的答案列表
        clean_question_bank()
        all_titletypes = driver.find_elements(By.XPATH,'//*[@id="submitForm"]/div')# 获取本页所有题型
        for i in all_titletypes:# i是某一个题型
            current_type = i.find_element(By.XPATH,'./h2').text# 获得题型
            print("正在处理的题型是{}".format(current_type))
            for index,current_title in enumerate(i.find_elements(By.XPATH,'./div')):# current_title是某个题,i.find_e找到了所有题
                # 开始具体题目的处理
                if "单选题" in current_type:
                    try:
                        for option in current_title.find_elements(By.XPATH,'./div[2]/div'):# 遍历具体题目的每一个选项
                            if answer_list[0] == option.text[0]:
                                ActionChains(driver).move_to_element(option).click().perform()
                                answer_list.pop(0)
                                time.sleep(0.5)
                                break
                    except Exception as e:
                        print("查找单选题过程中出错")
                        driver.switch_to.default_content()
                elif "多选题" in current_type:
                    try:
                        num =0
                        option_num = len(current_title.find_elements(By.XPATH, './div[2]/div'))# 获取选项数量
                        for option in current_title.find_elements(By.XPATH, './div[2]/div'):  # 遍历具体题目的每一个选项
                            num +=1
                            if  option.text[0] in answer_list[0]:
                                ActionChains(driver).move_to_element(option).click().perform()
                                time.sleep(0.5)

                            # 判断是否过了所有选项，确认后移除答案，并结束本次循环
                            if num >=option_num:
                                answer_list.pop(0)
                                break
                    except Exception as e:
                        print(f"多选题出错")
                        raise e
                elif "填空题" in current_type:
                    try:
                        get_iframe = current_title.find_element(By.XPATH,'./div[2]/div/div[1]/div/div[1]/div/div[2]/iframe')
                        driver.switch_to.frame(get_iframe)
                        answer_input = driver.find_element(By.XPATH, '/html/body/p')
                        answer_input.send_keys(answer_list[0])
                        driver.execute_script("arguments[0].dispatchEvent(new Event('input'));", answer_input)
                        # driver.execute_script(
                        #     "arguments[0].innerText = arguments[1]; arguments[0].dispatchEvent(new Event('input'));",
                        #     answer_input, answer_list[0])# 用js脚本直接修改
                        answer_list.pop(0)
                        driver.switch_to.default_content()
                        time.sleep(0.5)
                    except Exception as e:
                        print(f"填空题写入失败：{e}")
                        driver.switch_to.default_content()
                elif "判断题" in current_type:
                    for option in current_title.find_elements(By.XPATH, './div[2]/div'):
                        if answer_list[0] in option.text:
                            ActionChains(driver).move_to_element(option).click().perform()
                            answer_list.pop(0)
                            time.sleep(0.5)
                            break
                elif "简答题" in current_type:
                    try:
                        # 检测有没有图片，有的话直接跳过
                        img_tags = current_title.find_elements(By.TAG_NAME,'img')
                        if img_tags:
                            print("有图片，跳过")
                            continue

                        title_iframe = current_title.find_element(By.XPATH,'//*[@id="ueditor_0"]')# 找到简答题里的iframe
                        driver.switch_to.frame(title_iframe)
                        answer_input = driver.find_element(By.XPATH, '/html/body/p')
                        answer_input.send_keys(answer_list[0])
                        driver.execute_script("arguments[0].dispatchEvent(new Event('input'));", answer_input)
                        answer_list.pop(0)
                        driver.switch_to.default_content()
                        time.sleep(0.5)
                    except Exception as e:
                        print(f"简答题写入出错：{e}")
        up_button = driver.find_element(By.XPATH,'//*[@id="submitFocus"]/a[2]')# 找到提交按钮
        up_button.click()
        time.sleep(1)
        driver.find_element(By.XPATH,'//*[@id="popok"]').click()
        time.sleep(1)
    else:
        raise Exception("未找到DeepSeek的API")
    # 自动化写作业逻辑
    # 我说很简单
def clean_question_bank():
    global question_bank
    question_bank = []

if __name__ == "__main__":
    browser = init_browser()
    try:
        browser.get("https://passport2.chaoxing.com/login?fid=12&refer=http%3A%2F%2Fi.chaoxing.com%2Fbase%3Ft%3D1771763723377&space=2")
        visit_target_page(browser,*get_user_data())
        time.sleep(3)
    finally:
        browser.quit()
        # ---By 赵志恒,2026/4/17
