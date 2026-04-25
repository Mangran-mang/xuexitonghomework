from openai import OpenAI


class OpenDeepSeek:
    DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"  # DeepSeek 兼容 OpenAI 的接口地址

    def __init__(self, api_key):
        self.api_key = api_key

        self.client = OpenAI(
            api_key=self.api_key,  # 用 DeepSeek 的 API Key
            base_url=self.DEEPSEEK_BASE_URL  # 用 DeepSeek 的兼容接口地址
        )

    def ask_deepseek(self,question):
        try:
            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": question}],
                temperature=0.7,  # 随机性（0-2）
                max_tokens=1000  # 最大回答长度
            )
            answer = response.choices[0].message.content
            return answer

        except Exception as e:
            return f"调用失败：{str(e)}"

# 结束字符 = ["对话结束","不聊了","关闭对话"]

    def dialogue(self,question_bank):
        answer_list = []
        print("题库题数:",len(question_bank))
        # i = 0
        qtip = "判断题只需要回答对错,填空题直接回答填什么,单选题和多选题只用回复正确选项,简答题请回答80字以内的答案"
        for question in question_bank:
            # i +=1
            q_text = ""
            if question["options"]:
                options = question["options"]
                q_text = question["title"]+ ":"+options
            else:
                q_text = question["title"]

            answer = self.ask_deepseek(qtip+q_text)
            print("DeepSeek：",answer)
            answer_list.append(answer)
            # if i >=6:
            #     return answer_list
        return answer_list
