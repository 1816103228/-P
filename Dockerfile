FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

# 运行时通过环境变量注入 DEEPSEEK_API_KEY（参考 .env.example）
ENV DISABLE_SCHEDULER=0

CMD ["streamlit", "run", "main.py", "--server.headless=true", "--server.address=0.0.0.0", "--server.port=8501"]
