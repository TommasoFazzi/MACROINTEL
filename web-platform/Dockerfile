# Stage 1: Install dependencies
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# BuildKit cache mount: keeps npm package cache across builds (fast even with --no-cache)
RUN --mount=type=cache,target=/root/.npm \
    npm ci --legacy-peer-deps

# Stage 2: Build application
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules

# DEPLOY_SHA changes on every deploy → busts Docker layer cache from here onward,
# guaranteeing COPY and npm run build always execute with the latest source.
ARG DEPLOY_SHA=unknown
RUN echo "Deploy SHA: $DEPLOY_SHA"

COPY . .

# Build-time env vars (NEXT_PUBLIC_* only — baked into JS bundle)
ARG NEXT_PUBLIC_MAPBOX_TOKEN
ENV NEXT_PUBLIC_MAPBOX_TOKEN=$NEXT_PUBLIC_MAPBOX_TOKEN

RUN npm run build

# Stage 3: Production runner (minimal image)
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

RUN addgroup -S nextjs && adduser -S nextjs -G nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nextjs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nextjs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
