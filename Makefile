.PHONY: up down logs build shell

up:
	@echo "🚀 Đang khởi động AI Server..."
	docker compose up -d

down:
	@echo "🛑 Đang tắt Server..."
	docker compose down

logs:
	@echo "📋 Đang theo dõi log (Bấm Ctrl+C để thoát)..."
	docker compose logs -f api

build:
	@echo "🔨 Đang build lại Docker image..."
	docker compose build

shell:
	@echo "💻 Đang vào container..."
	docker exec -it vinuni-ai-agent bash
