FROM node:18-slim AS build

WORKDIR /app

ARG VITE_API_BASE=http://localhost:8080
ENV VITE_API_BASE=$VITE_API_BASE

COPY FE/package*.json ./
RUN npm ci

COPY FE/ .
RUN npm run build

FROM nginx:alpine AS runtime

COPY FE/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 3000


