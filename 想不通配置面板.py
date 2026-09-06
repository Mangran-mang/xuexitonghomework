"""学习通一键刷作业 · 配置面板

页签一「一键刷作业」：填写账号 / 密码 / 课程名，保存后自动登录刷未交作业。
页签二「刷当前考试」：接管你手动打开的考试答题页面，AI 自动作答当前页所有题目。
配置统一保存到「想不通账号信息.txt」，格式与原有脚本完全兼容。
"""

import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from tkinter import messagebox

try:
    import customtkinter as ctk
except ImportError:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "缺少依赖", "未安装 customtkinter，请先执行：\n\npip install customtkinter"
        )
    except Exception:
        print("未安装 customtkinter，请先执行: pip install customtkinter")
    sys.exit(1)


# ─────────────────────────────────────────────
# 路径与配置文件读写（兼容 PyInstaller 打包环境）
# ─────────────────────────────────────────────
BASE_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
CONFIG_FILE = "想不通账号信息.txt"
HW_SCRIPT = "想不通一键刷作业.py"
EXAM_SCRIPT = "想不通刷当前考试.py"

# 主脚本按行号取第 2~5 行（账号/密码/课程/API），行顺序不能变
TEMPLATE_LINES = [
    "注：不要修改原有文字，要刷的课程之间要用英文逗号隔开",
    "账号:",
    "密码:",
    "要刷的课程名:",
    "API:",
    "====================================",
    "上面原有的文字一个不要改，误触的话返回一下或者手动改回去就行",
    "注意你课程名中的大小写，程序是没问题的",
    "妥善保管你的API，不要公开给别人",
    "课程名字不需要特别全，比如你是分布式数据库，那你只要写分布式数，脚本会自己找到课程",
]

# (配置键, 文件前缀)——前缀匹配兼容中英文冒号
FIELD_PREFIXES = [
    ("zhanghao", "账号"),
    ("mima", "密码"),
    ("courses", "要刷的课程名"),
    ("api", "API"),
]

# 刷考试：带调试端口的独立 Edge
EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
EXAM_DEBUG_PORT = 9222
EXAM_USER_DATA_DIR = r"C:\EdgeTest"  # 独立数据目录，避免和日常使用的 Edge 冲突

EXAM_STEPS = (
    "① 点「启动考试模式 Edge」：弹出一个独立的 Edge 窗口，不影响你常用的 Edge\n"
    "② 在新窗口里登录学习通，打开考试（或作业）的答题页面\n"
    "③ 点「开始答题」：AI 自动作答当前页面的所有题目，进度看下方日志\n"
    "④ 答完脚本自动退出，浏览器保持打开——自己检查一遍，手动点提交"
)


def read_config():
    """从 txt 里读出现有配置，文件不存在返回 None。"""
    path = os.path.join(BASE_DIR, CONFIG_FILE)
    if not os.path.exists(path):
        return None
    cfg = {"zhanghao": "", "mima": "", "courses": "", "api": ""}
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f.read().splitlines():
            for key, prefix in FIELD_PREFIXES:
                if line.startswith(prefix + ":") or line.startswith(prefix + "："):
                    cfg[key] = line[len(prefix) + 1:].strip()
    return cfg


def write_config(zhanghao: str, mima: str, courses: str, api: str):
    """写回 txt：已有文件只替换对应前缀的行，其余文字原样保留；没有则用模板新建。"""
    path = os.path.join(BASE_DIR, CONFIG_FILE)
    values = {
        "账号": f"账号:{zhanghao}",
        "密码": f"密码:{mima}",
        "要刷的课程名": f"要刷的课程名:{courses}",
        "API": f"API:{api}",
    }

    original_lines = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            original_lines = f.read().splitlines()

    if original_lines and all(
        any(
            line.startswith(prefix + ":") or line.startswith(prefix + "：")
            for line in original_lines
        )
        for _, prefix in FIELD_PREFIXES
    ):
        replaced = set()
        new_lines = []
        for line in original_lines:
            hit = None
            for _, prefix in FIELD_PREFIXES:
                if prefix not in replaced and (
                    line.startswith(prefix + ":") or line.startswith(prefix + "：")
                ):
                    hit = prefix
                    break
            if hit:
                new_lines.append(values[hit])
                replaced.add(hit)
            else:
                new_lines.append(line)
    else:
        new_lines = list(TEMPLATE_LINES)
        for i, (_, prefix) in enumerate(FIELD_PREFIXES):
            new_lines[i + 1] = values[prefix]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")


# ─────────────────────────────────────────────
# 配置面板界面
# ─────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("学习通一键刷作业 · 配置面板")
        self.geometry("720x860")
        self.minsize(640, 780)

        self.hw_proc = None
        self.exam_proc = None
        self.log_queue = queue.Queue()

        self._build_ui()
        self._load_config()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_log)

    # ---------- 界面布局 ----------
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)  # 页签区随窗口拉伸

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 6))
        ctk.CTkLabel(
            header, text="学习通一键刷作业", font=("Microsoft YaHei UI", 22, "bold")
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="「一键刷作业」自动登录刷未交作业；「刷当前考试」接管你手动打开的答题页面。"
                 "配置保存在 想不通账号信息.txt，与原脚本完全兼容。",
            font=("Microsoft YaHei UI", 12),
            text_color="gray65",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(
            header,
            text="如果你节省下的时间只是去玩游戏，那这个脚本的存在就是错的",
            font=("Microsoft YaHei UI", 13, "italic"),
            text_color="#c9975c",
        ).pack(anchor="w", pady=(6, 0))

        # API Key（两个功能共用）
        api_frame = ctk.CTkFrame(self)
        api_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=8)
        api_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(api_frame, text="API Key", font=("Microsoft YaHei UI", 13, "bold")).grid(
            row=0, column=0, padx=(14, 6), pady=12
        )
        self.api_entry = ctk.CTkEntry(
            api_frame, placeholder_text="sk-...", show="•", height=36
        )
        self.api_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=12)
        self.api_switch = ctk.CTkSwitch(
            api_frame, text="显示", width=64, command=self._toggle_api
        )
        self.api_switch.grid(row=0, column=2, padx=(0, 8))
        self.test_btn = ctk.CTkButton(
            api_frame, text="测试", width=64, height=32, command=self._test_api
        )
        self.test_btn.grid(row=0, column=3, padx=(0, 14))

        # 两个功能页签
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=2, column=0, sticky="nsew", padx=20, pady=8)
        try:
            self.tabview.segmented_button.configure(font=("Microsoft YaHei UI", 13))
        except Exception:
            pass
        tab_hw = self.tabview.add("一键刷作业")
        tab_exam = self.tabview.add("刷当前考试")
        self._build_hw_tab(tab_hw)
        self._build_exam_tab(tab_exam)

        # 状态栏
        self.status_label = ctk.CTkLabel(
            self, text="就绪", anchor="w", font=("Microsoft YaHei UI", 12), text_color="gray65"
        )
        self.status_label.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 10))

    def _build_hw_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        # 账号 / 密码
        info = ctk.CTkFrame(tab)
        info.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        info.grid_columnconfigure(1, weight=1)
        info.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(info, text="账号").grid(row=0, column=0, padx=(14, 6), pady=12)
        self.account_entry = ctk.CTkEntry(info, placeholder_text="手机号", height=36)
        self.account_entry.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=12)

        ctk.CTkLabel(info, text="密码").grid(row=0, column=2, padx=(0, 6), pady=12)
        self.password_entry = ctk.CTkEntry(info, placeholder_text="密码", show="•", height=36)
        self.password_entry.grid(row=0, column=3, sticky="ew", padx=(0, 10), pady=12)
        self.password_switch = ctk.CTkSwitch(
            info, text="显示", width=64, command=self._toggle_password
        )
        self.password_switch.grid(row=0, column=4, padx=(0, 14))

        # 课程名
        course_frame = ctk.CTkFrame(tab)
        course_frame.grid(row=1, column=0, sticky="ew", padx=14, pady=6)
        course_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            course_frame, text="要刷的课程名（每行一个）", font=("Microsoft YaHei UI", 13, "bold")
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
        self.course_box = ctk.CTkTextbox(course_frame, height=88, font=("Microsoft YaHei UI", 13))
        self.course_box.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 4))
        ctk.CTkLabel(
            course_frame,
            text="不用写全名，能对上就行，例如「操作系统」；保存时自动转成英文逗号分隔。",
            font=("Microsoft YaHei UI", 11),
            text_color="gray60",
        ).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))

        # 操作按钮
        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 4))
        btns.grid_columnconfigure((0, 1, 2), weight=1, uniform="hwbtn")
        self.save_btn = ctk.CTkButton(btns, text="保存配置", height=40, command=self._save_config)
        self.save_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.hw_run_btn = ctk.CTkButton(
            btns, text="启动刷作业", height=40,
            fg_color="#2fa572", hover_color="#1a7a4d", command=self._start_hw,
        )
        self.hw_run_btn.grid(row=0, column=1, sticky="ew", padx=6)
        self.hw_stop_btn = ctk.CTkButton(
            btns, text="停止", height=40,
            fg_color="#b34141", hover_color="#7c2a2a",
            state="disabled", command=self._stop_hw,
        )
        self.hw_stop_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        # 运行日志
        self.hw_log = self._make_log_area(tab, row=3)

    def _build_exam_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        steps = ctk.CTkFrame(tab)
        steps.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        steps.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            steps, text="使用步骤", font=("Microsoft YaHei UI", 13, "bold")
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            steps, text=EXAM_STEPS, justify="left", anchor="w",
            font=("Microsoft YaHei UI", 13),
        ).grid(row=1, column=0, sticky="w", padx=14)
        ctk.CTkLabel(
            steps,
            text="提示：需要 driver/msedgedriver.exe 与 Edge 版本匹配；答题过程随时可点「停止」，已填的答案不会丢。",
            font=("Microsoft YaHei UI", 11),
            text_color="gray60",
            justify="left",
            anchor="w",
            wraplength=620,
        ).grid(row=2, column=0, sticky="w", padx=14, pady=(6, 10))

        btns = ctk.CTkFrame(tab, fg_color="transparent")
        btns.grid(row=1, column=0, sticky="ew", padx=14, pady=(6, 4))
        btns.grid_columnconfigure((0, 1, 2), weight=1, uniform="exbtn")
        self.edge_btn = ctk.CTkButton(
            btns, text="启动考试模式 Edge", height=40, command=self._launch_exam_edge
        )
        self.edge_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.exam_btn = ctk.CTkButton(
            btns, text="开始答题", height=40,
            fg_color="#2fa572", hover_color="#1a7a4d", command=self._start_exam,
        )
        self.exam_btn.grid(row=0, column=1, sticky="ew", padx=6)
        self.exam_stop_btn = ctk.CTkButton(
            btns, text="停止", height=40,
            fg_color="#b34141", hover_color="#7c2a2a",
            state="disabled", command=self._stop_exam,
        )
        self.exam_stop_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0))

        # 运行日志
        self.exam_log = self._make_log_area(tab, row=2)

    def _make_log_area(self, parent, row: int):
        log_frame = ctk.CTkFrame(parent)
        log_frame.grid(row=row, column=0, sticky="nsew", padx=14, pady=(4, 14))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(log_frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            header, text="运行日志", font=("Microsoft YaHei UI", 13, "bold")
        ).pack(side="left")
        box = ctk.CTkTextbox(log_frame, font=("Microsoft YaHei UI", 12))
        box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 12))
        box.configure(state="disabled")

        def clear():
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.configure(state="disabled")

        ctk.CTkButton(
            header, text="清空", width=56, height=26,
            fg_color="gray40", hover_color="gray30", command=clear,
        ).pack(side="right")
        return box

    # ---------- 小工具 ----------
    def _set_status(self, text: str):
        self.status_label.configure(text=text)

    def _append_to(self, box, text: str):
        box.configure(state="normal")
        box.insert("end", text + "\n")
        box.see("end")
        box.configure(state="disabled")

    def _toggle_password(self):
        self.password_entry.configure(show="" if self.password_switch.get() else "•")

    def _toggle_api(self):
        self.api_entry.configure(show="" if self.api_switch.get() else "•")

    def _poll_log(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "api_result":
                    ok, msg = payload
                    self.test_btn.configure(state="normal", text="测试")
                    self._append_to(self.hw_log, ("✅ " if ok else "❌ ") + msg)
                    self._set_status(msg)
                elif kind == "hw_end":
                    self._append_to(self.hw_log, f"—— 脚本已退出，退出码 {payload} ——")
                    self.hw_run_btn.configure(state="normal")
                    self.hw_stop_btn.configure(state="disabled")
                    self._set_status(f"刷作业脚本已退出（退出码 {payload}）")
                elif kind == "exam_end":
                    self._append_to(self.exam_log, f"—— 答题脚本已退出，退出码 {payload} ——")
                    self.exam_btn.configure(state="normal")
                    self.exam_stop_btn.configure(state="disabled")
                    self._set_status("刷考试已结束，请在浏览器里检查答案并手动提交")
                elif kind.startswith("hw"):
                    self._append_to(self.hw_log, payload)
                elif kind.startswith("exam"):
                    self._append_to(self.exam_log, payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_log)

    # ---------- 读取 / 保存 ----------
    def _load_config(self):
        cfg = read_config()
        if cfg is None:
            self._append_to(self.hw_log, "未找到「想不通账号信息.txt」，保存时会自动生成新文件。")
            self._set_status("就绪（新配置）")
            return
        self.account_entry.insert(0, cfg["zhanghao"])
        self.password_entry.insert(0, cfg["mima"])
        self.api_entry.insert(0, cfg["api"])
        for name in cfg["courses"].split(","):
            name = name.strip()
            if name:
                self.course_box.insert("end", name + "\n")
        self._append_to(self.hw_log, "已从「想不通账号信息.txt」读取现有配置。")
        self._set_status("已读取现有配置")

    def _collect_fields(self):
        courses = self.course_box.get("1.0", "end").replace("，", ",")
        names = [line.strip() for line in courses.splitlines() if line.strip()]
        return (
            self.account_entry.get().strip(),
            self.password_entry.get().strip(),
            ",".join(names),
            self.api_entry.get().strip(),
            len(names),
        )

    def _save_config(self, mode: str = "hw") -> bool:
        zhanghao, mima, courses, api, course_count = self._collect_fields()
        if mode == "hw":
            if not zhanghao or not mima or not api:
                messagebox.showwarning("信息不完整", "账号、密码、API Key 都不能为空。")
                return False
            if course_count == 0 and not messagebox.askyesno(
                "课程为空", "没有填写任何课程名，确定仍要保存吗？"
            ):
                return False
        else:
            if not api:
                messagebox.showwarning("缺少 API Key", "刷考试至少需要填写 API Key。")
                return False
        write_config(zhanghao, mima, courses, api)
        self._append_to(self.hw_log, f"已保存：账号 {zhanghao}，课程 [{courses}]")
        self._set_status("✅ 已保存到 想不通账号信息.txt")
        return True

    # ---------- API 测试 ----------
    def _test_api(self):
        api = self.api_entry.get().strip()
        if not api:
            messagebox.showwarning("缺少 API Key", "请先填写 API Key。")
            return
        self.test_btn.configure(state="disabled", text="测试中")

        def work():
            ok, msg = False, ""
            try:
                from openai import OpenAI

                client = OpenAI(api_key=api, base_url="https://api.deepseek.com")
                resp = client.chat.completions.create(
                    model="deepseek-chat",
                    max_tokens=8,
                    timeout=20,
                    messages=[{"role": "user", "content": "请只回复：OK"}],
                )
                text = (resp.choices[0].message.content or "").strip()
                ok, msg = True, f"API 可用，模型回复：{text}"
            except Exception as e:
                msg = f"API 测试失败：{e}"
            self.log_queue.put(("api_result", (ok, msg)))

        threading.Thread(target=work, daemon=True).start()

    # ---------- 子进程通用逻辑 ----------
    def _python_command(self):
        if not getattr(sys, "frozen", False):
            return [sys.executable]
        for name in ("python", "py"):
            path = shutil.which(name)
            if path:
                return [path, "-3"] if name == "py" else [path]
        return None

    def _spawn(self, script_name: str, extra_args, kind: str):
        """启动脚本子进程并把输出转发到对应日志区，失败返回 None。
        成品模式（面板已打包）优先启动同目录下的同名 exe，目标电脑无需装 Python。"""
        script = os.path.join(BASE_DIR, script_name)
        exe = os.path.splitext(script)[0] + ".exe"

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        if getattr(sys, "frozen", False) and os.path.exists(exe):
            try:
                proc = subprocess.Popen(
                    [exe] + list(extra_args),
                    cwd=os.path.dirname(exe),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except Exception as e:
                messagebox.showerror("启动失败", str(e))
                return None
            self.log_queue.put((kind, f"—— 已启动 {os.path.basename(exe)}（PID {proc.pid}）——"))
            threading.Thread(target=self._reader, args=(proc, kind), daemon=True).start()
            return proc

        if not os.path.exists(script):
            messagebox.showerror("找不到脚本", f"未找到 {script_name}：\n{script}")
            return None
        cmd = self._python_command()
        if cmd is None:
            messagebox.showerror(
                "未找到 Python",
                "打包环境下启动脚本需要电脑上装有 Python，\n"
                "并用 pip install -r requirements.txt 装好依赖；\n"
                "或者把 想不通一键刷作业.exe / 想不通刷当前考试.exe\n"
                "放到配置面板同一个文件夹里（成品包自带）。",
            )
            return None

        try:
            proc = subprocess.Popen(
                cmd + [script] + list(extra_args),
                cwd=os.path.dirname(script),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            messagebox.showerror("启动失败", str(e))
            return None

        self.log_queue.put((kind, f"—— 已启动 {script_name}（PID {proc.pid}）——"))
        threading.Thread(target=self._reader, args=(proc, kind), daemon=True).start()
        return proc

    def _reader(self, proc, kind: str):
        try:
            for line in iter(proc.stdout.readline, ""):
                self.log_queue.put((kind, line.rstrip("\r\n")))
        finally:
            proc.stdout.close()
            code = proc.wait()
            self.log_queue.put((kind + "_end", code))

    # ---------- 刷作业 ----------
    def _start_hw(self):
        if self.hw_proc and self.hw_proc.poll() is None:
            return
        if not self._save_config("hw"):
            return
        proc = self._spawn(HW_SCRIPT, [], "hw")
        if proc is None:
            return
        self.hw_proc = proc
        self.hw_run_btn.configure(state="disabled")
        self.hw_stop_btn.configure(state="normal")
        self._set_status("刷作业脚本运行中…（浏览器会自动打开）")

    def _stop_hw(self):
        if self.hw_proc and self.hw_proc.poll() is None:
            if not messagebox.askyesno(
                "停止", "确定要停止刷作业脚本吗？\n（已经打开的 Edge 窗口可能需要手动关闭）"
            ):
                return
            self.hw_proc.terminate()
            self._set_status("正在停止刷作业脚本…")

    # ---------- 刷考试 ----------
    def _edge_port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", EXAM_DEBUG_PORT), timeout=0.5):
                return True
        except OSError:
            return False

    def _launch_exam_edge(self):
        if self._edge_port_open():
            self._append_to(self.exam_log, "检测到调试端口 9222 的 Edge 已在运行，无需重复启动。")
            self._set_status("考试模式 Edge 已在运行")
            return
        edge = next((p for p in EDGE_CANDIDATES if os.path.exists(p)), None) or shutil.which("msedge")
        if not edge:
            messagebox.showerror(
                "未找到 Edge",
                "没有找到 msedge.exe，请手动启动：\n"
                f'msedge --remote-debugging-port={EXAM_DEBUG_PORT} '
                f'--user-data-dir="{EXAM_USER_DATA_DIR}"',
            )
            return
        subprocess.Popen(
            [
                edge,
                f"--remote-debugging-port={EXAM_DEBUG_PORT}",
                f"--user-data-dir={EXAM_USER_DATA_DIR}",
                "--no-first-run",
            ]
        )
        self._append_to(self.exam_log, "正在启动考试模式 Edge…")

        def wait_port():
            for _ in range(20):
                if self._edge_port_open():
                    self.log_queue.put(
                        ("exam", "✅ 考试模式 Edge 已就绪：请在窗口里登录学习通并打开答题页面，然后点「开始答题」。")
                    )
                    return
                time.sleep(0.5)
            self.log_queue.put(("exam", "⚠ 没检测到调试端口 9222，若窗口没弹出来请手动启动 Edge。"))

        threading.Thread(target=wait_port, daemon=True).start()

    def _start_exam(self):
        if self.exam_proc and self.exam_proc.poll() is None:
            return
        if not self._save_config("exam"):
            return
        if not self._edge_port_open():
            messagebox.showwarning(
                "未检测到考试 Edge",
                "没有检测到调试端口 9222 的 Edge。\n"
                "请先点「启动考试模式 Edge」，登录学习通并打开答题页面，再点「开始答题」。",
            )
            return
        proc = self._spawn(EXAM_SCRIPT, ["--auto-exit"], "exam")
        if proc is None:
            return
        self.exam_proc = proc
        self.exam_btn.configure(state="disabled")
        self.exam_stop_btn.configure(state="normal")
        self._set_status("刷考试运行中…答完请手动检查提交")

    def _stop_exam(self):
        if self.exam_proc and self.exam_proc.poll() is None:
            if not messagebox.askyesno(
                "停止", "确定要停止答题吗？\nEdge 窗口会保持打开，已填的答案不会丢。"
            ):
                return
            self.exam_proc.terminate()
            self._set_status("正在停止答题…")

    # ---------- 退出 ----------
    def _on_close(self):
        running = [
            name for name, proc in (("刷作业", self.hw_proc), ("刷考试", self.exam_proc))
            if proc and proc.poll() is None
        ]
        if running and not messagebox.askyesno(
            "退出", f"{'、'.join(running)}还在运行，退出会同时结束它，确定吗？"
        ):
            return
        for proc in (self.hw_proc, self.exam_proc):
            if proc and proc.poll() is None:
                proc.terminate()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
