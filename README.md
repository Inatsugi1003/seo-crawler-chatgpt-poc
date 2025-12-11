# Site Crawler × ChatGPT Analysis (Streamlit Cloud)

このリポジトリは、指定したWebサイトを自動でクロールし、ChatGPT（OpenAI API）と連携してSEO・UX観点から分析を行うStreamlitアプリです。

---

## 🚀 デプロイ方法（Streamlit Community Cloud）

1. GitHubにこのリポジトリをpush  
2. Streamlit Cloudで新規アプリを作成  
   - **Main file path**：`app.py`  
3. 「App Settings → Secrets」に以下を登録：

```toml
OPENAI_API_KEY="sk-xxxx"
