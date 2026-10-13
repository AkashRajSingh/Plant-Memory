# --- build the web app ---------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- API + MCP + CLI -------------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=web /web/dist ./web/dist
EXPOSE 8765
CMD ["python", "-m", "plant_memory", "serve", "--host", "0.0.0.0", "--port", "8765"]
