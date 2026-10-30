# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY . .
RUN npx ng build --configuration production

FROM nginx:1.27-alpine
RUN apk add --no-cache curl
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY docker/40-env-js.sh /docker-entrypoint.d/40-env-js.sh
RUN chmod +x /docker-entrypoint.d/40-env-js.sh
COPY --from=build /app/dist/app/browser /usr/share/nginx/html
ENV BACKEND_URL=http://backend:8080 \
    APP_REALTIME=signalr \
    APP_MAP_STYLE_URL="" \
    APP_MAP_CENTER="" \
    APP_MAP_ZOOM="" \
    APP_MAP_ORIGINS="https://tile.openstreetmap.org"
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD curl -fsS http://localhost/healthz || exit 1
