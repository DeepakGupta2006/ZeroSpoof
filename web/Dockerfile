FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY . .
# NEXT_PUBLIC_* values are baked into the browser bundle at build time.
ARG NEXT_PUBLIC_MEDIAPIPE_WASM_URL
ARG NEXT_PUBLIC_FACE_MODEL_URL
ENV NEXT_PUBLIC_MEDIAPIPE_WASM_URL=$NEXT_PUBLIC_MEDIAPIPE_WASM_URL NEXT_PUBLIC_FACE_MODEL_URL=$NEXT_PUBLIC_FACE_MODEL_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app ./
USER node
EXPOSE 3000
CMD ["npm", "start"]
