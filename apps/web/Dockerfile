FROM node:22.19.0-alpine AS build
WORKDIR /ui
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY src/ src/
COPY tsconfig.json .
RUN npx --no-install tsc && npx --no-install esbuild src/main.tsx --bundle --minify --outfile=/dist/app.js --define:process.env.NODE_ENV='"production"'
FROM nginx:1.28.0-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /dist/ /usr/share/nginx/html/
COPY index.html /usr/share/nginx/html/
