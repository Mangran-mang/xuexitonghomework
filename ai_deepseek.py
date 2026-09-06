from openai import OpenAI

# 所有题型共用的提示语（作业和考试脚本都走这个前缀）
Q_TIP = "判断题只需要回答对错,填空题直接回答填什么,单选题和多选题只用回复正确选项,简答题请回答120字以内的答案。所有答案不要回答多余信息"


class OpenDeepSeek:
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"  # DeepSeek 兼容 OpenAI 的接口地址

    def __init__(self, api_key):
        self.api_key = api_key

        self.client = OpenAI(
            api_key=self.api_key,  # 用 DeepSeek 的 API Key
            base_url=self.DEEPSEEK_BASE_URL  # 用 DeepSeek 的兼容接口地址
        )

    def ask_deepseek(self, question):
        for attempt in range(2):
            try:
                response = self.client.chat.completions.create(
                    model="deepseek-v4-flash",
                    messages=[{"role": "user", "content": question}],
                    temperature=0.2,  # 随机性（0-2）
                    max_tokens=4000  # 最大回答长度（留足推理空间，太小会返回空内容）
                )
                answer = (response.choices[0].message.content or "").strip()
                if answer:
                    return answer
                if attempt == 0:
                    print("（模型返回了空答案，重试一次…）")
            except Exception as e:
                if attempt == 1:
                    return f"调用失败：{str(e)}"
                print(f"（调用失败，重试一次：{str(e)[:80]}）")
        return ""

    def ask_one(self, question):
        """回答单道题，question 格式：{"title": 题干, "options": 选项(str/list)或 None}"""
        q_text = question.get("title", "")
        options = question.get("options")
        if options:
            if isinstance(options, (list, tuple)):
                options = " ".join(str(o) for o in options)
            q_text = f"{q_text}:{options}"
        return self.ask_deepseek(Q_TIP + q_text)

    def dialogue(self, question_bank):
        answer_list = []
        print("题库题数:", len(question_bank))
        for question in question_bank:
            answer = self.ask_one(question)
            print("DeepSeek：", answer)
            answer_list.append(answer)
        return answer_list
