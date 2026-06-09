# Multi-stage: build React → serve with nginx
FROM node:20-alpine AS builder

WORKDIR /app
COPY apps/dashboard/package*.json ./
RUN npm install

COPY apps/dashboard/ ./
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx config that proxies /api → orchestrator:8000
COPY apps/dashboard/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
