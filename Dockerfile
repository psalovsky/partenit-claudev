FROM python:3.11-slim

# Git only — pipeline worker uses HTTP LLM (default deepseek-chat), not Claude Code CLI.
# To restore Anthropic CLI: add Node + npm i -g @anthropic-ai/claude-code (see worker.py comments).
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

RUN git config --global user.name "Claudev" && \
    git config --global user.email "claudev@noreply.github.com"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8090

# Direct Python entry avoids ./entrypoint.sh failing on Windows CRLF in the shell script.
CMD ["python", "main.py"]
