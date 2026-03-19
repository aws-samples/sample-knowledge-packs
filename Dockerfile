FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root user (S-5)
RUN useradd -r -s /bin/false appuser

# Copy application code
COPY scripts/ scripts/

USER appuser

# AgentCore expects MCP servers at 0.0.0.0:8000/mcp
ENV MCP_TRANSPORT=streamable-http
EXPOSE 8000

CMD ["python", "scripts/kb_server.py"]
